"""Reparse: rebuild the interpretation layer from stored raw bytes alone.

Capture stores raw ``.jsonl`` lines byte-faithfully forever; interpretation (see
:mod:`introspect.ingest.interpret`) derives Message/ContentBlock/TokenUsage/SessionEvent rows
from them and can be wrong — a schema bug, a fixed model field, a new record type learned
after the fact. Reparse is the recovery lever: it throws away every derived row and rebuilds
them from ``raw_records.raw_line`` using the *current* :func:`introspect.schema.parse_line` +
:func:`introspect.ingest.interpret.apply`, so nothing here ever touches a source file on disk
— by the time this runs, the original transcript files may not even exist anymore. The DB is
the archive.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from introspect.ingest import interpret
from introspect.ingest.capture import utcnow
from introspect.models import (
    ChatSession,
    ContentBlock,
    Message,
    ParseAnomaly,
    RawRecord,
    SessionEvent,
    TokenUsage,
)
from introspect.schema import SCHEMA_VERSION, parse_line

CHUNK_SIZE = 500

# Anomaly kinds that originate downstream of the raw bytes — purely a function of
# (schema version, raw_line) — and are therefore safe to delete and let reparse regenerate.
# Everything else is a capture-phase judgment about source *identity* (a uuid rewritten under
# our feet, a file diverging/reappearing, an ingest-time I/O failure) that reparse has no way
# to recompute from raw_line alone and MUST NOT delete.
_INTERPRETATION_ANOMALY_KINDS = frozenset(
    {
        "invalid_json",
        "unknown_record_type",
        "unknown_field",
        "validation_error",
        "interpret_failure",
        "whitespace_line",
    }
)


@dataclass
class ReparseStats:
    records_reparsed: int
    anomalies_before: int
    anomalies_after: int


def reparse_all(db: Session) -> ReparseStats:
    """Rebuild every interpretation row from ``raw_records.raw_line``, current schema.

    Deletes ALL interpretation rows in FK-safe child-first order: ``content_blocks``,
    ``token_usage``, THEN ``messages``; ``session_events`` independently (the same order
    :func:`interpret.remove_interpretation_for_source_file` uses per source file, applied
    here globally). Deletes ONLY interpretation-kind ``parse_anomalies`` (``invalid_json``,
    ``unknown_record_type``, ``unknown_field``, ``validation_error``, ``interpret_failure``,
    ``whitespace_line``) — capture-phase integrity anomalies (``uuid_content_conflict``,
    ``source_diverged``, ``source_reappeared``, ``file_ingest_failure``) are history reparse
    cannot regenerate from raw bytes and MUST survive. Resets the ``ChatSession`` title/time
    caches (``ai_title``, ``custom_title``, ``started_at``, ``last_activity_at`` -> ``None``)
    so :func:`interpret.apply`'s folds rebuild them deterministically instead of merging into
    stale state.

    Then re-runs :func:`introspect.schema.parse_line` + :func:`interpret.apply` over every
    ``raw_records`` row ordered by ``(source_file_id, line_number)``, in chunks of 500 with a
    commit per chunk. Whitespace-only lines are the one exception to ``parse_line``: they are
    graded via :func:`interpret.grade_whitespace_line` — the same helper capture uses — so a
    no-op reparse of an unchanged archive is status-idempotent. ``apply()`` is itself generation-aware (only a record whose
    ``source_file.is_primary`` is true produces rows) and stamps
    ``parsed_with_schema_version`` + ``parse_status`` on every record it touches, so reparse
    does not duplicate that primary check or that stamping. ``raw_line`` bytes are the only
    input this function reads — source files are never opened, and may no longer exist.

    A record whose re-interpretation raises is isolated in its own SAVEPOINT so the failure
    can't discard its chunk-mates' already-staged work, and is stamped ``parse_status``
    ``"anomaly"`` plus an ``interpret_failure`` anomaly instead (mirrors how capture's own
    call to ``apply()`` is guarded).
    """
    anomalies_before = db.query(ParseAnomaly).count()

    _delete_all_interpretation_rows(db)
    _delete_interpretation_anomalies(db)
    _reset_session_caches(db)
    db.commit()

    raw_ids = [
        rid
        for (rid,) in db.query(RawRecord.id)
        .order_by(RawRecord.source_file_id, RawRecord.line_number)
        .all()
    ]

    records_reparsed = 0
    for start in range(0, len(raw_ids), CHUNK_SIZE):
        for raw_id in raw_ids[start : start + CHUNK_SIZE]:
            raw = db.get(RawRecord, raw_id)
            _reparse_one(db, raw)
            records_reparsed += 1
        db.commit()

    anomalies_after = db.query(ParseAnomaly).count()
    return ReparseStats(records_reparsed, anomalies_before, anomalies_after)


def _reparse_one(db: Session, raw: RawRecord) -> None:
    """Re-interpret a single raw record, isolating a failure to just this record."""
    if interpret.is_whitespace_line(raw.raw_line):
        # Same canonical grading capture uses — NOT parse_line, which would misgrade the
        # line as invalid_json and make a no-op reparse mutate statuses/severities.
        interpret.grade_whitespace_line(db, raw)
        return

    pr = parse_line(raw.raw_line)
    try:
        with db.begin_nested():  # SAVEPOINT: a failure here rolls back only this record
            interpret.apply(db, pr, raw)
    except Exception as exc:  # noqa: BLE001 -- must never abort the rest of the reparse
        raw.parsed_with_schema_version = SCHEMA_VERSION
        raw.parse_status = "anomaly"
        db.add(
            ParseAnomaly(
                raw_record_id=raw.id,
                source_file_id=raw.source_file_id,
                severity="error",
                kind="interpret_failure",
                detail={"error": str(exc)},
                schema_version=SCHEMA_VERSION,
                created_at=utcnow(),
            )
        )
        return

    for anomaly in pr.anomalies:
        db.add(
            ParseAnomaly(
                raw_record_id=raw.id,
                source_file_id=raw.source_file_id,
                severity=anomaly.severity,
                kind=anomaly.kind,
                detail=anomaly.detail,
                schema_version=SCHEMA_VERSION,
                created_at=utcnow(),
            )
        )


def _delete_all_interpretation_rows(db: Session) -> None:
    """Child-first FK-safe wipe of every derived row, globally (see reparse_all docstring)."""
    db.query(ContentBlock).delete(synchronize_session=False)
    db.query(TokenUsage).delete(synchronize_session=False)
    db.query(Message).delete(synchronize_session=False)
    db.query(SessionEvent).delete(synchronize_session=False)


def _delete_interpretation_anomalies(db: Session) -> None:
    db.query(ParseAnomaly).filter(
        ParseAnomaly.kind.in_(_INTERPRETATION_ANOMALY_KINDS)
    ).delete(synchronize_session=False)


def _reset_session_caches(db: Session) -> None:
    """Clear cached title/time folds so re-interpretation rebuilds them from scratch."""
    db.query(ChatSession).update(
        {
            ChatSession.ai_title: None,
            ChatSession.custom_title: None,
            ChatSession.started_at: None,
            ChatSession.last_activity_at: None,
        },
        synchronize_session=False,
    )
