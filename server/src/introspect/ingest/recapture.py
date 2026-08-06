"""Recapture: byte-reconciled repair of a source file's raw-record layer.

Before the pretty-JSONL reassembly fix (compat spec §2), a hand-pretty-printed transcript
record was captured one PHYSICAL LINE at a time -- every continuation line of the pretty JSON
failed to parse on its own and was stored as its own ``invalid_json``-anomalous ``RawRecord``,
"shattering" one logical record into many broken ones. The bytes were never lost (capture is
always byte-faithful), but the record BOUNDARIES drawn over them were wrong, and no amount of
:func:`introspect.ingest.reparse.reparse_all` can fix that -- reparse only rebuilds
interpretation from the STORED raw lines, it never re-draws where one record ends and the next
begins.

Recapture is the layer below reparse: it re-splits a source file's bytes from disk using the
CURRENT reader (:func:`introspect.ingest.reader.read_complete_units`, brace-balance aware),
proves the re-split reconstructs EXACTLY the bytes already on file (the gate, below), and only
then swaps the shattered ``raw_records`` for correctly-bounded ones and re-interprets them --
using the same :func:`introspect.ingest.capture._capture_chunk` path a normal import uses, so a
healed file is indistinguishable from one that was captured correctly from the start.

**The gate.** Recapture never trusts that the file on disk still says what capture originally
read -- a source file can be edited, truncated, or replaced between the incident and the
repair. Before touching a single row, the freshly re-split bytes (``[0, checkpoint)`` only --
anything the checkpoint hasn't claimed yet belongs to a future ``import``, not to recapture)
must concatenate to EXACTLY the bytes already stored for this file. A mismatch, or a
reassembled unit that straddles the checkpoint boundary (its continuation lines were appended
to the file AFTER capture last set the checkpoint), refuses outright: :class:`RecaptureStats`
reports ``reconciled=False`` and NOTHING in the database changes -- no rows deleted, no rows
added, no :class:`~introspect.models.ImportRun` row -- whether the caller asked for a real run
or a ``dry_run``. A refused run is a no-op by construction, not a partial one.

**The swap.** Once the gate passes and ``dry_run`` is False, the file's raw records are
replaced whole: interpretation is torn down per-file the same way a demoted generation's is
(:func:`introspect.ingest.interpret.remove_interpretation_for_source_file` -- de-index FTS,
then content_blocks/token_usage/messages/session_events, all scoped to THIS source file only,
never the whole archive the way :func:`introspect.ingest.reparse.reparse_all` does), this
file's interpretation-class ``parse_anomalies`` are deleted (never the capture-phase
bookkeeping kinds -- ``source_diverged`` / ``source_reappeared`` / ``file_ingest_failure`` /
``uuid_content_conflict`` are history recapture cannot regenerate and must survive), the
session's cached title/time-bounds columns are reset so re-interpretation rebuilds them
instead of folding into stale state, and finally every stored ``RawRecord`` for the file is
deleted. None of this is committed yet -- it rides in the SAME open transaction that
:func:`~introspect.ingest.capture._capture_chunk`'s own sacred commit finalizes, together with
the freshly re-split records it inserts: either the whole swap becomes durable at once, or (if
re-insertion raises before that commit) nothing does, and the original shattered rows are
exactly as they were. Re-insertion uses ``bypass_dedup=True`` -- the same
divergence-regeneration precedent ``capture._handle_divergence`` uses -- because the file's own
prior (shattered) rows must never dedup-skip its own correctly-bounded replacements.

``byte_offset_checkpoint`` / ``prefix_hash`` / ``last_size`` are never specially preserved or
restored: ``_capture_chunk`` recomputes them exactly as it always does, and because the gate
already proved the re-split bytes are identical to what was stored, those recomputed values
land back on the exact same numbers. There is nothing to protect.

**Interpretation isolation.** ``_capture_chunk``'s own interpretation step
(``capture._interpret_chunk``) contains a failing record's exception, but its containment is
CHUNK-WIDE: on one record's failure it ``db.rollback()``s the whole in-progress chunk, which
discards every OTHER not-yet-committed record's successful interpretation in that same chunk
too (their ``parsed_with_schema_version`` stamp reverts to NULL along with it). A normal
``import`` run tolerates this because :func:`introspect.ingest.run._sweep_unparsed` cleans up
any NULL-stamped survivor on the NEXT run; recapture calls ``_capture_chunk`` directly, outside
that orchestration, so it must provide its own equivalent -- :func:`_sweep_unparsed_for_file`,
run immediately after the re-insertion loop, re-interprets this file's still-NULL-stamped
records one at a time inside a SAVEPOINT (the same ``reparse._reparse_one`` idiom), so a single
genuinely-bad record can no longer take its chunk-mates' healed interpretation down with it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from introspect.export import _resolve_source_file
from introspect.ingest import interpret
from introspect.ingest.capture import CHUNK_SIZE, CaptureStats, _capture_chunk, utcnow
from introspect.ingest.reader import RawUnit, read_complete_units
from introspect.ingest.reparse import _INTERPRETATION_ANOMALY_KINDS
from introspect.models import (
    ChatSession,
    ImportRun,
    ParseAnomaly,
    Project,
    RawRecord,
    Transcript,
)
from introspect.schema import SCHEMA_VERSION, parse_line


@dataclass
class RecaptureStats:
    """Outcome of one :func:`recapture_file` call.

    The five fields the task brief mandates, plus two reporting extras (mirrors
    :class:`introspect.ingest.run.ImportSummary`'s "extra fields are permitted" precedent):
    ``path`` (the resolved source file's path, always set once resolution succeeds) and
    ``reason`` (set only when ``reconciled`` is False -- the refuse diagnosis).
    """

    records_before: int
    records_after: int
    anomalies_before: int
    anomalies_after: int
    reconciled: bool
    path: str | None = None
    reason: str | None = None


def recapture_file(
    db: Session,
    session_uuid: str,
    kind: str = "main",
    agent_hex_id: str | None = None,
    dry_run: bool = False,
) -> RecaptureStats:
    """Re-split a source file's bytes with the current reader and heal shattered records.

    Resolves the transcript's source file exactly as export does (:func:`introspect.export._resolve_source_file`
    -- same "which file is this session" question; raises ``SessionNotFoundError`` /
    ``TranscriptNotFoundError`` identically when the session/transcript is unknown), gates the
    re-split against the stored bytes (see module docstring), and -- gate passing, ``dry_run``
    False -- swaps the file's raw records for correctly reassembled ones and re-interprets
    them, recording an :class:`~introspect.models.ImportRun` row (``trigger='recapture'``).
    """
    source_file = _resolve_source_file(db, session_uuid, kind, agent_hex_id)
    path = Path(source_file.path)
    checkpoint = source_file.byte_offset_checkpoint

    stored_rows = (
        db.query(RawRecord.raw_line)
        .filter(RawRecord.source_file_id == source_file.id)
        .order_by(RawRecord.line_number)
        .all()
    )
    records_before = len(stored_rows)
    anomalies_before = _anomaly_count_for_file(db, source_file.id)

    new_units, straddled = _resplit_within_checkpoint(path, checkpoint)
    if straddled:
        return RecaptureStats(
            records_before, records_before, anomalies_before, anomalies_before,
            reconciled=False, path=str(path),
            reason="reassembly crosses the capture checkpoint — run introspect import first",
        )

    stored_bytes = b"".join(raw_line for (raw_line,) in stored_rows)
    new_bytes = b"".join(u.data for u in new_units)
    if new_bytes != stored_bytes:
        return RecaptureStats(
            records_before, records_before, anomalies_before, anomalies_before,
            reconciled=False, path=str(path),
            reason="stored raw bytes do not match a fresh re-split of the source file",
        )

    records_after = len(new_units)
    if dry_run:
        # The gate passed -- report the would-be swap without touching the database. A
        # refused gate (above) and a passing dry-run (here) are BOTH zero-mutation: dry_run
        # never gets far enough to write an ImportRun row either way.
        return RecaptureStats(
            records_before, records_after, anomalies_before, anomalies_before,
            reconciled=True, path=str(path),
        )

    transcript = db.get(Transcript, source_file.transcript_id)
    project = db.get(Project, source_file.project_id)
    baseline_anomaly_id = db.query(func.max(ParseAnomaly.id)).scalar() or 0
    started_at = utcnow()

    # --- The swap: torn down here, rebuilt by _capture_chunk below -- staged in the ONE
    # transaction _capture_chunk's own sacred commit finalizes (see module docstring).
    interpret.remove_interpretation_for_source_file(db, source_file.id)
    # NOTE(claude): scoped to interpretation-class kinds only (never source_diverged /
    # source_reappeared / file_ingest_failure / uuid_content_conflict -- capture-phase history
    # recapture cannot regenerate). uuid_content_conflict is the one bookkeeping kind that
    # carries a raw_record_id INSIDE this file (a census of production data found zero rows of
    # this kind, but it is possible in principle: two source files disagreeing about the same
    # record_uuid's content). If such a row exists for a record about to be deleted below,
    # PRAGMA foreign_keys=ON (see db.py) means that delete raises IntegrityError and the whole
    # swap rolls back -- a safe hard stop, not corruption, but a stop. Converting it instead
    # (e.g. re-linking it file-level with its detail preserved) is a real design decision an
    # owner should make deliberately, not something to improvise here.
    db.query(ParseAnomaly).filter(
        ParseAnomaly.source_file_id == source_file.id,
        ParseAnomaly.kind.in_(_INTERPRETATION_ANOMALY_KINDS),
    ).delete(synchronize_session=False)
    _reset_session_cache_for_transcript(db, transcript)
    # NOTE(claude): see the NOTE above the anomaly delete -- a surviving uuid_content_conflict
    # anomaly whose raw_record_id points into THIS file's about-to-be-deleted rows raises
    # IntegrityError here under PRAGMA foreign_keys=ON, aborting the swap (transaction rolls
    # back to the pre-recapture shattered state; see the module docstring's "one transaction"
    # discussion). Not reachable by any of the 5 binding tests; flagged for the archive owner.
    db.query(RawRecord).filter(RawRecord.source_file_id == source_file.id).delete(
        synchronize_session=False
    )

    stats = CaptureStats(0, 0, 0)
    running = hashlib.sha256()
    file_line_number = 0
    size_at_start = path.stat().st_size
    for start in range(0, len(new_units), CHUNK_SIZE):
        chunk = new_units[start : start + CHUNK_SIZE]
        file_line_number = _capture_chunk(
            db, project, transcript, source_file, chunk, running,
            file_line_number, size_at_start, stats, bypass_dedup=True,
        )

    # _capture_chunk's own interpretation step can lose a chunk-mate's successful
    # interpretation to a sibling's failure (see module docstring's "Interpretation isolation").
    # recapture doesn't go through run_import's next-run self-healing sweep, so it provides its
    # own, immediately, via a per-record SAVEPOINT (the reparse._reparse_one idiom).
    _sweep_unparsed_for_file(db, source_file.id)

    this_run_anomalies = db.query(ParseAnomaly).filter(ParseAnomaly.id > baseline_anomaly_id)
    anomaly_count = this_run_anomalies.count()
    has_errors = this_run_anomalies.filter(ParseAnomaly.severity == "error").count() > 0
    status = "errors" if has_errors else "ok"

    db.add(
        ImportRun(
            trigger="recapture",
            started_at=started_at,
            finished_at=utcnow(),
            files_seen=1,
            records_added=records_after,
            records_skipped_duplicate=0,
            anomaly_count=anomaly_count,
            status=status,
        )
    )
    db.commit()

    return RecaptureStats(
        records_before, records_after,
        anomalies_before, _anomaly_count_for_file(db, source_file.id),
        reconciled=True, path=str(path),
    )


# --- Helpers ------------------------------------------------------------------------------


def _resplit_within_checkpoint(path: Path, checkpoint: int) -> tuple[list[RawUnit], bool]:
    """Re-split ``path`` from offset 0 with the CURRENT reader, keeping only units fully
    inside ``[0, checkpoint)`` -- bytes beyond the checkpoint belong to a future
    ``introspect import``, not to recapture. Returns ``(units, straddled)``: ``straddled`` is
    True iff a unit's span crosses the checkpoint boundary (its continuation lines were
    appended to the file after capture last set the checkpoint) -- recapture cannot safely
    resolve that ambiguity and must refuse.
    """
    units: list[RawUnit] = []
    for unit in read_complete_units(path, from_offset=0):
        if unit.end_offset <= checkpoint:
            units.append(unit)
        elif unit.start_offset < checkpoint:
            return units, True
        else:
            break
    return units, False


def _anomaly_count_for_file(db: Session, source_file_id: int) -> int:
    """Every ``parse_anomalies`` row scoped to this source file, any kind."""
    return db.query(ParseAnomaly).filter(ParseAnomaly.source_file_id == source_file_id).count()


def _sweep_unparsed_for_file(db: Session, source_file_id: int) -> None:
    """Re-interpret this file's still-NULL-stamped records, one at a time, isolated.

    The scoped, immediate analogue of :func:`introspect.ingest.run._sweep_unparsed` -- needed
    because :func:`recapture_file` drives ``_capture_chunk`` directly rather than through
    ``run_import``'s orchestration, which normally provides this self-healing pass on the NEXT
    import. Mirrors that function's retry policy too: a NULL-stamped record that ALREADY carries
    an ``interpret_failure`` anomaly (i.e. ``_capture_chunk``'s own ``_interpret_chunk`` already
    tried and failed it) is excluded -- its failure is deterministic under the current schema,
    so retrying it here would just churn a duplicate anomaly; :func:`_reinterpret_one` handles
    a record failing for the FIRST time in this sweep.
    """
    has_prior_failure = (
        db.query(ParseAnomaly.id)
        .filter(
            ParseAnomaly.raw_record_id == RawRecord.id,
            ParseAnomaly.kind == "interpret_failure",
        )
        .exists()
    )
    raw_ids = [
        rid
        for (rid,) in db.query(RawRecord.id)
        .filter(
            RawRecord.source_file_id == source_file_id,
            RawRecord.parsed_with_schema_version.is_(None),
            ~has_prior_failure,
        )
        .order_by(RawRecord.line_number)
        .all()
    ]
    for start in range(0, len(raw_ids), CHUNK_SIZE):
        for raw_id in raw_ids[start : start + CHUNK_SIZE]:
            raw = db.get(RawRecord, raw_id)
            if raw is not None:
                _reinterpret_one(db, raw)
        db.commit()


def _reinterpret_one(db: Session, raw: RawRecord) -> None:
    """Re-interpret a single raw record inside a SAVEPOINT -- verbatim the
    ``reparse._reparse_one`` idiom (reparse.py:129-168), so a failure here rolls back only
    THIS record and can never discard a chunk-mate's already-staged interpretation.
    """
    if interpret.is_whitespace_line(raw.raw_line):
        interpret.grade_whitespace_line(db, raw)
        return

    pr = parse_line(raw.raw_line)
    try:
        with db.begin_nested():  # SAVEPOINT: a failure here rolls back only this record
            interpret.apply(db, pr, raw)
    except Exception as exc:  # noqa: BLE001 -- must never abort the rest of the sweep
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


def _reset_session_cache_for_transcript(db: Session, transcript: Transcript) -> None:
    """Clear the transcript's session's cached title/time folds so re-interpretation rebuilds
    them from scratch -- the scoped analogue of
    :func:`introspect.ingest.reparse._reset_session_caches`, touching only the one session
    this file's transcript belongs to (never the whole archive).

    NOTE(claude): a session with MULTIPLE transcripts (a main + one or more subagents) folds
    ai_title/custom_title/started_at/last_activity_at from ALL of them. Recapturing just one
    transcript resets the WHOLE session's cache but only re-folds THIS transcript's records,
    so a title or time bound that came from a DIFFERENT transcript of the same session is
    dropped until that other transcript is also recaptured, reparsed, or re-imported (any of
    which re-extends/rebuilds the bounds). This mirrors the task brief's explicit scoping
    ruling ("ChatSession cache reset scopes to the file's transcript's session only") and is a
    documented, accepted narrow gap -- none of the binding tests exercise a multi-transcript
    session's recapture.
    """
    session = db.get(ChatSession, transcript.session_id)
    if session is None:
        return
    session.ai_title = None
    session.custom_title = None
    session.started_at = None
    session.last_activity_at = None
