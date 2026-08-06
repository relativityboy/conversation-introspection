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
    db.query(ParseAnomaly).filter(
        ParseAnomaly.source_file_id == source_file.id,
        ParseAnomaly.kind.in_(_INTERPRETATION_ANOMALY_KINDS),
    ).delete(synchronize_session=False)
    _reset_session_cache_for_transcript(db, transcript)
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
