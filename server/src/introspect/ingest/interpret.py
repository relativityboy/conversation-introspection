"""Interpretation: turn a parsed record into the normalized reading-room rows.

Capture stores raw bytes byte-faithfully; interpretation is the *downstream* layer that reads
a :class:`~introspect.schema.ParseResult` and materializes ``Message`` / ``ContentBlock`` /
``TokenUsage`` / ``SessionEvent`` rows plus session time bounds. It runs in a transaction
*separate* from capture (see :mod:`introspect.ingest.capture`): this function only stages rows
on ``db`` — it never commits, and it MAY raise; the caller (``_interpret_chunk``) rolls back and
records an ``interpret_failure`` anomaly so a bad line can never endanger captured bytes.

Design decisions binding this module (spec-owner resolutions):

* **Provenance on every path.** EVERY return path stamps ``raw.parsed_with_schema_version``
  and ``raw.parse_status`` (from the ParseResult). A NULL schema stamp means "never attempted"
  and is swept by a later task; an anomaly/None-record/non-primary line is *attempted* (stamped)
  but produces no interpretation rows.
* **Generation-awareness.** Only records whose ``SourceFile.is_primary`` is true are
  interpreted. A demoted/duplicate generation (backup, superseded rewrite) is stamped but
  contributes no rows, so one ``record_uuid`` never yields two Messages across generations.
* **Snapshot blobs stay in the archive.** A ``file-history-snapshot`` event's payload excludes
  the opaque ``snapshot`` field — those bytes live in ``raw_records`` only, never here.

Signature is pinned by the task brief; keep it exactly ``apply(db, pr, raw)``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from introspect.models import (
    ChatSession,
    ContentBlock,
    Message,
    ParseAnomaly,
    RawRecord,
    SessionEvent,
    SourceFile,
    TokenUsage,
    Transcript,
)
from introspect.schema import SCHEMA_VERSION, ParseResult, parse_line
from introspect.schema.authorship import AuthorshipContext, ToolUseRef, classify
from introspect.search import get_search_index

# Records that carry a conversational envelope and become a Message + ContentBlocks.
_CONVERSATIONAL = frozenset({"user", "assistant", "system", "attachment"})


def apply(db: Session, pr: ParseResult, raw: RawRecord) -> None:
    """Interpret one captured record into normalized rows. Stages on ``db``; never commits.

    Every path stamps provenance on ``raw``. A record that is an anomaly, unparsed, or belongs
    to a non-primary source-file generation is stamped only. A primary conversational record
    becomes a ``Message`` (+ ``ContentBlock`` rows from ``record.blocks()``, + a ``TokenUsage``
    row when assistant usage is present) and folds its timestamp into the session's
    ``started_at`` / ``last_activity_at`` bounds. A thin-meta record becomes a ``SessionEvent``;
    ``ai-title`` / ``custom-title`` additionally refresh the cached ``ChatSession`` title.
    """
    # Provenance is stamped on EVERY path, including the early returns below.
    raw.parsed_with_schema_version = SCHEMA_VERSION
    raw.parse_status = pr.status

    if pr.record is None or pr.status == "anomaly":
        # Nothing to interpret: unknown type, invalid JSON, or a validation failure.
        return None

    # Generation-awareness: only the primary generation produces interpretation rows.
    source_file = db.get(SourceFile, raw.source_file_id)
    if source_file is None or not source_file.is_primary:
        return None

    if pr.record_type in _CONVERSATIONAL:
        _apply_conversational(db, pr, raw)
    else:
        _apply_thin_meta(db, pr, raw)
    return None


def is_whitespace_line(raw_line: bytes) -> bool:
    """True for a whitespace-only line (torn-write residue; see :func:`grade_whitespace_line`)."""
    return raw_line.strip() == b""


def grade_whitespace_line(db: Session, raw: RawRecord) -> None:
    """Canonically grade a whitespace-only captured line. Stages on ``db``; never commits.

    A whitespace-only line is NEVER handed to ``parse_line`` (it would misgrade as
    ``invalid_json``, error severity): it is benign torn-write residue, graded exactly one
    info-severity ``whitespace_line`` anomaly with ``parse_status`` ``"partial"`` and a
    provenance stamp (so the future NULL-stamp sweep never re-attempts it). BOTH capture and
    reparse route whitespace lines through here, so a no-op reparse of an unchanged archive
    is status-idempotent — the two paths cannot drift apart.
    """
    raw.parsed_with_schema_version = SCHEMA_VERSION
    raw.parse_status = "partial"
    db.add(
        ParseAnomaly(
            raw_record_id=raw.id,
            source_file_id=raw.source_file_id,
            severity="info",
            kind="whitespace_line",
            detail={},
            schema_version=SCHEMA_VERSION,
            created_at=datetime.now(timezone.utc),
        )
    )


def remove_interpretation_for_source_file(db: Session, source_file_id: int) -> None:
    """Delete every interpretation row derived from a source file's raw records.

    Used when a generation is demoted (see capture's ``_handle_divergence``): the superseding
    generation re-ingests the same ``record_uuid`` values, so the old generation's Messages must
    go or the reading room would show cross-generation duplicates. The old generation's blocks
    are de-indexed FIRST (while their text is still readable), then rows are removed child-first
    (``content_blocks`` and ``token_usage`` before ``messages``; ``session_events`` independently)
    to respect foreign keys. Stages the deletes on ``db``; the caller owns the commit.
    """
    raw_ids = [
        rid
        for (rid,) in db.query(RawRecord.id)
        .filter(RawRecord.source_file_id == source_file_id)
        .all()
    ]
    if not raw_ids:
        return

    message_ids = [
        mid
        for (mid,) in db.query(Message.id).filter(Message.raw_record_id.in_(raw_ids)).all()
    ]
    if message_ids:
        block_ids = [
            bid
            for (bid,) in db.query(ContentBlock.id)
            .filter(ContentBlock.message_id.in_(message_ids))
            .all()
        ]
        # De-index BEFORE deleting the content_blocks rows: FTS5 external-content keeps no copy
        # of the text, so delete_for_blocks must re-read it from the still-present rows. Doing
        # this after the delete would corrupt the index (see search.fts5.delete_for_blocks).
        get_search_index().delete_for_blocks(db, block_ids)
        db.query(ContentBlock).filter(ContentBlock.message_id.in_(message_ids)).delete(
            synchronize_session=False
        )
        db.query(TokenUsage).filter(TokenUsage.message_id.in_(message_ids)).delete(
            synchronize_session=False
        )
        db.query(Message).filter(Message.id.in_(message_ids)).delete(synchronize_session=False)

    db.query(SessionEvent).filter(SessionEvent.raw_record_id.in_(raw_ids)).delete(
        synchronize_session=False
    )


# --- Conversational records -------------------------------------------------------------


def _apply_conversational(db: Session, pr: ParseResult, raw: RawRecord) -> None:
    record = pr.record
    message_model = getattr(record, "message", None)
    timestamp = _parse_timestamp(getattr(record, "timestamp", None))

    message = Message(
        raw_record_id=raw.id,
        transcript_id=raw.transcript_id,
        record_uuid=pr.record_uuid,
        parent_uuid=getattr(record, "parentUuid", None),
        timestamp=timestamp,
        type=pr.record_type,
        model=getattr(message_model, "model", None),
        cwd=getattr(record, "cwd", None),
        git_branch=getattr(record, "gitBranch", None),
        request_id=getattr(record, "requestId", None),
    )
    db.add(message)
    db.flush()  # assign message.id so child rows can reference it

    new_blocks: list[ContentBlock] = []
    for index, block in enumerate(record.blocks()):
        content_block = ContentBlock(
            message_id=message.id,
            block_index=index,
            block_kind=block.kind,
            text_content=block.text,
            tool_name=block.tool_name,
            tool_use_id=block.tool_use_id,
            is_error=block.is_error,
            payload=block.payload,
        )
        db.add(content_block)
        new_blocks.append(content_block)

    if new_blocks:
        # Index the new blocks in THIS transaction (index_blocks self-filters to the text-only
        # predicate, so passing every block id is correct — non-text ids are skipped). apply()
        # never flushed its ContentBlock rows before, but index_blocks reads their text by id,
        # so flush to assign ids first.
        # NOTE(claude): the index rows share capture's interpretation transaction, so
        # _interpret_chunk's rollback-on-failure discards them together with the blocks they
        # describe — indexing inherits apply()'s all-or-nothing containment for free.
        db.flush()
        get_search_index().index_blocks(db, [b.id for b in new_blocks])

    usage = getattr(message_model, "usage", None)
    if usage is not None:
        db.add(
            TokenUsage(
                message_id=message.id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
            )
        )

    _fold_session_bounds(db, raw.transcript_id, timestamp)


def _fold_session_bounds(db: Session, transcript_id: int, timestamp: datetime | None) -> None:
    """Extend the session's [started_at, last_activity_at] window to include ``timestamp``."""
    if timestamp is None:
        return
    session = _session_for_transcript(db, transcript_id)
    if session is None:
        return
    if session.started_at is None or timestamp < session.started_at:
        session.started_at = timestamp
    if session.last_activity_at is None or timestamp > session.last_activity_at:
        session.last_activity_at = timestamp


# --- Thin-meta records ------------------------------------------------------------------


def _apply_thin_meta(db: Session, pr: ParseResult, raw: RawRecord) -> None:
    record = pr.record
    transcript = db.get(Transcript, raw.transcript_id)
    # Prefer the transcript's session (FK-guaranteed to exist from capture) over the record's
    # own sessionId echo, which may drift or be absent.
    session_id = (
        transcript.session_id if transcript is not None else getattr(record, "sessionId", None)
    )

    if pr.record_type == "file-history-snapshot":
        # The opaque snapshot blob is archive-only; it lives in raw_records, never here.
        payload = record.model_dump(mode="json", exclude={"snapshot"})
    else:
        payload = record.model_dump(mode="json")

    db.add(
        SessionEvent(
            raw_record_id=raw.id,
            session_id=session_id,
            event_kind=pr.record_type,
            payload=payload,
        )
    )

    if session_id is None:
        return
    if pr.record_type == "ai-title":
        session = db.get(ChatSession, session_id)
        if session is not None:
            session.ai_title = record.aiTitle
    elif pr.record_type == "custom-title":
        session = db.get(ChatSession, session_id)
        if session is not None:
            session.custom_title = record.customTitle


# --- Helpers ----------------------------------------------------------------------------


def _session_for_transcript(db: Session, transcript_id: int) -> ChatSession | None:
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        return None
    return db.get(ChatSession, transcript.session_id)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime; never raise.

    Missing or unparseable input yields ``None``. A trailing ``Z`` is normalized, and a naive
    value is assumed to already be UTC — matching :class:`introspect.db.UTCDateTime` so a
    freshly-parsed timestamp compares cleanly against a DB-loaded (always aware) one.
    """
    if not value:
        return None
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- Authorship post-pass (spec 2026-08-07 §4) -------------------------------------------
#
# classify_pending runs AFTER interpretation (apply() above never touches authorship_kind),
# driven purely by `authorship_kind IS NULL` -- an incremental import only sees its own new
# rows, and reparse (which wipes every Message and rebuilds from raw_records) sees all of
# them again. The classifier itself (introspect.schema.authorship) is DB-free and pure; the
# DB access -- building the per-transcript tool_use map and re-parsing raw_line -- lives here.


def _transcript_context(db: Session, transcript_id: int) -> AuthorshipContext:
    """Whole-transcript tool_use map, built in memory BEFORE classifying any record --
    this is what makes rules 2-4 order-independent (a production tool_result can precede
    its tool_use in file order) and O(1) per record instead of a full content_blocks scan.
    """
    rows = db.execute(
        select(ContentBlock.tool_use_id, ContentBlock.tool_name, ContentBlock.payload)
        .join(Message, ContentBlock.message_id == Message.id)
        .where(
            Message.transcript_id == transcript_id,
            ContentBlock.block_kind == "tool_use",
            ContentBlock.tool_use_id.is_not(None),
        )
    ).all()
    tool_uses = {}
    for tool_use_id, tool_name, payload in rows:
        skill = None
        if tool_name == "Skill" and isinstance(payload, dict):
            value = payload.get("skill")
            skill = value if isinstance(value, str) else None
        tool_uses[tool_use_id] = ToolUseRef(name=tool_name or "", skill=skill)
    kind = db.scalar(select(Transcript.kind).where(Transcript.id == transcript_id))
    return AuthorshipContext(transcript_kind=kind or "main", tool_uses=tool_uses)


def classify_pending(db: Session) -> Counter:
    """Classify every ``messages`` row with a NULL ``authorship_kind``. Idempotent post-pass
    called by both import and reparse after interpretation completes; returns the census by
    kind. Re-parses each pending row's ``raw_line`` (interpretation itself never keeps the
    parsed record around) and feeds it to the pure, DB-free classifier alongside the
    transcript's whole tool_use map.
    """
    census: Counter = Counter()
    pending_transcripts = db.scalars(
        select(Message.transcript_id).where(Message.authorship_kind.is_(None)).distinct()
    ).all()
    for transcript_id in pending_transcripts:
        ctx = _transcript_context(db, transcript_id)
        rows = db.execute(
            select(Message.id, RawRecord.raw_line)
            .join(RawRecord, Message.raw_record_id == RawRecord.id)
            .where(
                Message.transcript_id == transcript_id,
                Message.authorship_kind.is_(None),
            )
        ).all()
        for message_id, raw_line in rows:
            text = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else raw_line
            )
            authorship = classify(parse_line(text).record, ctx)
            db.execute(
                update(Message)
                .where(Message.id == message_id)
                .values(
                    authorship_kind=authorship.kind,
                    authorship_basis=authorship.basis,
                    authorship_detail=authorship.detail,
                )
            )
            census[authorship.kind] += 1
    return census
