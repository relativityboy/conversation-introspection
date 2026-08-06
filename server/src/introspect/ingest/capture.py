"""Capture: the archive-critical core. Raw transcript bytes into SQLite, byte-faithfully.

The Claude Code CLI deletes transcripts out from under us, so capture's one job is to get
every complete line into ``raw_records`` before that happens — losslessly and crash-safely.
Everything else (what a line *means*) is a downstream concern that must never endanger the
bytes.

Two invariants make that safe:

* **Transaction split (capture is sacred).** For each chunk we commit the raw records *and*
  the file's checkpoint/prefix_hash/last_size in one transaction, then run interpretation in
  a *separate* transaction. A failure while interpreting writes an anomaly and moves on; it
  can never roll back a captured line.
* **Byte-faithful resume.** Export is pure concatenation of stored ``raw_line`` bytes in
  ``line_number`` order, so those bytes must equal the file. We only advance the checkpoint
  past lines we have durably stored (or deliberately skipped as duplicates). The torn-tail
  rule — a trailing chunk still being written must not be captured until the writer catches
  up — is the reader's job, not capture's: ``read_complete_units``
  (:mod:`introspect.ingest.reader`) defers a newline-less trailing line AND an EOF-open
  pretty-JSON reassembly buffer alike, so every unit this module receives is already
  complete. ``prefix_hash`` is the sha256 of the FILE's ingested prefix (bytes
  ``[0:checkpoint]``) — Task 7 detects divergence by comparing it against a fresh hash of the
  same byte range.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from introspect.ingest import interpret
from introspect.ingest.discovery import DiscoveredFile
from introspect.ingest.reader import RawUnit, read_complete_units
from introspect.models import (
    ChatSession,
    ParseAnomaly,
    Project,
    RawRecord,
    SourceFile,
    Transcript,
)
from introspect.schema import SCHEMA_VERSION, parse_line

CHUNK_SIZE = 500
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def utcnow() -> datetime:
    """Timezone-aware current UTC instant (what every datetime column stores)."""
    return datetime.now(timezone.utc)


@dataclass
class CaptureStats:
    records_added: int
    records_skipped_duplicate: int
    anomalies: int


def capture_file(db: Session, f: DiscoveredFile) -> CaptureStats:
    """Ensure the Project/Session/Transcript/SourceFile rows exist, then ingest new lines.

    See the module docstring for the two invariants (transaction split, byte-faithful
    resume). Returns per-file counts; the caller aggregates them into an ImportRun.
    """
    now = utcnow()
    size_at_start = f.path.stat().st_size

    project = _get_or_create_project(db, f.project_slug, now)
    session = _get_or_create_session(db, f.session_uuid, project)
    transcript = _get_or_create_transcript(db, f, session)
    source_file = _get_or_create_source_file(db, f, project, transcript, size_at_start, now)
    # Persist structural rows (FK targets + last_seen) before we touch a single raw byte, so
    # an empty file still records that it was seen and later runs find committed parents.
    db.commit()

    running, file_line_number, diverged = _resume_prefix_state(
        f.path, source_file.byte_offset_checkpoint, source_file.prefix_hash
    )
    # reappeared: the max-generation row for this path is not live (gone_at_source, or —
    # defensively — diverged, which shouldn't be reachable as max gen) yet the file is back
    # on disk. Prefix identity decides what "back" means below.
    reappeared = source_file.status != "active"
    if diverged:
        # SAME path, changed prefix: the CLI rewrote the file under us (live), or the path
        # came back holding different bytes (reappeared). Either way: freeze the old
        # generation and re-ingest the whole file into a fresh one (see _handle_divergence),
        # bypassing dedup so the new primary is complete and byte-for-byte exportable.
        # Grafting the new file's tail onto the dead row would silently corrupt its export.
        source_file = _handle_divergence(
            db, f, source_file, project, transcript, size_at_start, now, reappeared=reappeared
        )
        running, file_line_number = hashlib.sha256(), 0
    elif reappeared:
        # Prefix intact: the gone file was restored byte-identically (the common
        # restore-from-backup case). Clean resume on the SAME row: reactivate it and let
        # normal tail ingest continue from the stored checkpoint.
        _reactivate_source_file(db, source_file)

    stats = CaptureStats(0, 0, 0)
    # NOTE(claude): the torn-tail guard that used to live here (a trailing newline-less
    # chunk kept only if valid JSON) is now the reader's job — read_complete_units defers
    # (never yields) a torn tail OR an EOF-open reassembly buffer, so every unit reaching
    # this loop is already complete. See reader.py's module + read_complete_units docstrings.
    units = read_complete_units(f.path, from_offset=source_file.byte_offset_checkpoint)
    exhausted = False
    while not exhausted:
        chunk: list[RawUnit] = []
        for unit in units:
            chunk.append(unit)
            if len(chunk) >= CHUNK_SIZE:  # CHUNK_SIZE now counts units, not raw lines
                break
        else:
            exhausted = True
        if not chunk:
            break
        file_line_number = _capture_chunk(
            db, project, transcript, source_file, chunk, running,
            file_line_number, size_at_start, stats, bypass_dedup=diverged,
        )
    return stats


# --- Source liveness ---------------------------------------------------------------------


def detect_gone(db: Session, discovered: list[DiscoveredFile]) -> int:
    """Flip ACTIVE SourceFiles whose path vanished (not rediscovered AND not on disk).

    A file is "gone at source" when the CLI deleted it: it is absent from the current
    ``discovered`` list AND no longer exists on disk. Both conditions are required — a path
    a partial scan merely failed to enumerate but that is still on disk is left alone. Such
    rows get status ``gone_at_source`` and a ``gone_detected_at`` stamp.

    This is bookkeeping about the *source only*: it NEVER touches the ``raw_records`` already
    captured from that file — the archive's whole point is that those bytes outlive the file.
    Only ``active`` rows are considered, so a second sweep flips nothing (idempotent). Returns
    the count flipped.
    """
    discovered_paths = {str(f.path) for f in discovered}
    now = utcnow()
    flipped = 0
    for sf in db.query(SourceFile).filter_by(status="active").all():
        if sf.path in discovered_paths or Path(sf.path).exists():
            continue
        sf.status = "gone_at_source"
        sf.gone_detected_at = now
        flipped += 1
    if flipped:
        db.commit()
    return flipped


# --- Per-chunk capture + interpretation -------------------------------------------------


def _capture_chunk(
    db: Session,
    project: Project,
    transcript: Transcript,
    source_file: SourceFile,
    chunk: list[RawUnit],
    running: "hashlib._Hash",
    file_line_number: int,
    size_at_start: int,
    stats: CaptureStats,
    bypass_dedup: bool = False,
) -> int:
    """Store one chunk's raw records + checkpoint in a single (sacred) transaction.

    Then interpretation runs for the same chunk in a *separate* transaction so it can never
    roll capture back. Returns the advanced ``file_line_number``.

    ``bypass_dedup`` is set ONLY by a divergence re-ingest (see ``_handle_divergence``):
    the new generation must capture every line even though the old generation's records
    (same transcript, same uuids) still exist — routing it through dedup would skip them all
    and leave the new primary sparse, breaking reconstruction. Normal capture keeps dedup.
    """
    captured: list[tuple[object, RawRecord, list[tuple]]] = []

    for unit in chunk:
        # NOTE(claude): line_number is the 1-based FILE-position ordinal of the unit's FIRST
        # file line — skipped (deduped) units consume their ordinal(s) too, so stored
        # line_numbers may have gaps. Export ORDER BY line_number is unaffected by gaps, and
        # the uuid-less dedup key (transcript, sha, line_number) then means "same content at
        # the same file position" on both sides. A reassembled unit additionally consumes
        # `line_span` ordinals in one jump — its interior lines never get a row of their own,
        # so the NEXT unit's line_number picks up right after them, gap-free.
        line_no = file_line_number + 1
        file_line_number += unit.line_span
        raw = unit.data
        sha = hashlib.sha256(raw).hexdigest()
        # prefix_hash covers EVERY byte read from the file — stored and dedup-skipped alike:
        # it is the hash of file[0:checkpoint], which is what Task 7's divergence check
        # recomputes and compares against.
        running.update(raw)
        pr, record_type, record_uuid, version, parse_status, anomaly_specs = _classify(raw)

        if not bypass_dedup:
            is_dup, conflict_spec = _dedup_or_conflict(
                db, transcript.id, source_file, record_uuid, sha, line_no
            )
            if is_dup:
                stats.records_skipped_duplicate += 1
                continue
            if conflict_spec is not None:
                anomaly_specs = [*anomaly_specs, conflict_spec]

        record = RawRecord(
            source_file_id=source_file.id,
            transcript_id=transcript.id,
            line_number=line_no,
            byte_offset=unit.start_offset,
            raw_line=raw,
            line_sha256=sha,
            record_type=record_type,
            record_uuid=record_uuid,
            detected_cli_version=version,
            parsed_with_schema_version=None,  # stamped by the real interpret.apply (Task 8)
            parse_status=parse_status,
            reassembled=unit.reassembled,
            ingested_at=utcnow(),
        )
        db.add(record)
        _backfill_project_cwd(project, pr)
        captured.append((pr, record, anomaly_specs))

    checkpoint = chunk[-1].end_offset  # every unit in the chunk was consumed (stored or skipped)

    db.flush()  # assign RawRecord ids so anomalies can link to them
    for _pr, record, specs in captured:
        for severity, kind, detail, schema_version in specs:
            db.add(
                ParseAnomaly(
                    raw_record_id=record.id,
                    source_file_id=source_file.id,
                    severity=severity,
                    kind=kind,
                    detail=detail,
                    schema_version=schema_version,
                    created_at=utcnow(),
                )
            )
            stats.anomalies += 1

    source_file.byte_offset_checkpoint = checkpoint
    source_file.prefix_hash = running.hexdigest()
    source_file.last_size = max(size_at_start, checkpoint)
    source_file.last_seen_at = utcnow()
    stats.records_added += len(captured)
    db.commit()  # CAPTURE IS SACRED — bytes + checkpoint durable before anything interprets

    _interpret_chunk(db, source_file, captured, stats)
    return file_line_number


def _interpret_chunk(
    db: Session,
    source_file: SourceFile,
    captured: list[tuple[object, RawRecord, list[tuple]]],
    stats: CaptureStats,
) -> None:
    """Interpret a committed chunk in its own transaction. Never rolls capture back."""
    for pr, record, _specs in captured:
        if pr is None:  # whitespace-only line: canonical grading shared with reparse
            interpret.grade_whitespace_line(db, record)
            stats.anomalies += 1
            continue
        try:
            interpret.apply(db, pr, record)
        except Exception as exc:  # noqa: BLE001 -- interpretation must never escape capture
            # NOTE(claude): rollback first, so a mid-record failure in the real apply()
            # can't commit that record's partial interpretation rows. This also discards
            # earlier not-yet-committed work in this chunk — successes AND any staged
            # whitespace grades (their anomaly + stamp roll back too, transiently
            # over-counting stats.anomalies). All of it is regenerable interpretation
            # state: stamps revert to NULL, so the orchestrator's unparsed-row sweep and
            # reparse (per-record SAVEPOINTs) regrade them. Deliberate trade: whitespace
            # grading stays OUT of the sacred capture transaction to keep capture pure.
            # Capture itself committed before we got here; raw bytes are never at risk.
            db.rollback()
            db.add(
                ParseAnomaly(
                    raw_record_id=record.id,
                    source_file_id=source_file.id,
                    severity="error",
                    kind="interpret_failure",
                    detail={"error": str(exc)},
                    schema_version=SCHEMA_VERSION,
                    created_at=utcnow(),
                )
            )
            record.parse_status = "anomaly"
            stats.anomalies += 1
            db.commit()  # persist the failure marker before moving to the next record
    db.commit()


# --- Line classification & dedup --------------------------------------------------------


def _classify(raw: bytes):
    """Return (parse_result_or_None, record_type, record_uuid, version, parse_status, specs).

    A whitespace-only line is torn-write residue: captured, but never handed to the schema
    parser — its grading (info ``whitespace_line`` anomaly + stamp) happens in
    ``_interpret_chunk`` via :func:`interpret.grade_whitespace_line`, the SAME helper reparse
    uses, so a later reparse regrades it identically.
    """
    if interpret.is_whitespace_line(raw):
        return None, None, None, None, "partial", []
    pr = parse_line(raw)
    specs = [(a.severity, a.kind, a.detail, SCHEMA_VERSION) for a in pr.anomalies]
    return pr, pr.record_type, pr.record_uuid, pr.detected_cli_version, pr.status, specs


def _dedup_or_conflict(
    db: Session,
    transcript_id: int,
    source_file: SourceFile,
    record_uuid: str | None,
    sha: str,
    file_line_number: int,
) -> tuple[bool, tuple | None]:
    """Same-transcript duplicate check. Returns ``(is_duplicate, conflict_anomaly_spec)``.

    Dedup exists to skip other COPIES of a transcript, never bytes of the file itself —
    export concatenates a source file's stored lines, so skipping a same-file line is
    unrepairable capture loss (it would re-skip on every future run). Hence BOTH branches
    require the match to come from a *different* source file.

    A uuid-bearing line is a duplicate when its (record_uuid, line_sha256) already exists in
    the transcript from a different source file. The uuid existing with only DIFFERENT shas
    is NOT a duplicate: the line is captured, but flagged with an error-severity
    ``uuid_content_conflict`` anomaly (someone rewrote history under the same uuid — the
    archive keeps both and says so). A byte-identical repeat within the SAME file is stored
    silently: the uuid+bytes pair is already known, so it is no content conflict either.

    A uuid-less line is a duplicate only when the same (line_sha256, line_number) already
    exists in the transcript from a different source file. line_number is the file-position
    ordinal, so this means "identical content at the same position in another copy of this
    transcript" (e.g. a .bak replaying its main's lines, metadata lines included).
    """
    if record_uuid is not None:
        existing = (
            db.query(RawRecord.line_sha256, RawRecord.source_file_id, SourceFile.path)
            .join(SourceFile, RawRecord.source_file_id == SourceFile.id)
            .filter(
                RawRecord.transcript_id == transcript_id,
                RawRecord.record_uuid == record_uuid,
            )
            .all()
        )
        if any(
            existing_sha == sha and sfid != source_file.id
            for existing_sha, sfid, _path in existing
        ):
            return True, None
        if existing and not any(existing_sha == sha for existing_sha, _sfid, _path in existing):
            detail = {
                "uuid": record_uuid,
                "existing_sha": existing[0][0],
                "incoming_sha": sha,
                "existing_path": existing[0][2],
                "incoming_path": source_file.path,
            }
            return False, ("error", "uuid_content_conflict", detail, None)
        return False, None
    dup = (
        db.query(RawRecord.id)
        .filter(
            RawRecord.transcript_id == transcript_id,
            RawRecord.line_sha256 == sha,
            RawRecord.line_number == file_line_number,
            RawRecord.source_file_id != source_file.id,
        )
        .first()
    )
    return dup is not None, None


def _backfill_project_cwd(project: Project, pr) -> None:
    """Set Project.resolved_cwd from an envelope's cwd the first time we see one (idempotent)."""
    if pr is None or pr.record is None:
        return
    cwd = getattr(pr.record, "cwd", None)
    if cwd and project.resolved_cwd is None:
        project.resolved_cwd = cwd


# --- Resume state -----------------------------------------------------------------------


def _resume_prefix_state(
    path: Path, checkpoint: int, expected_prefix_hash: str
) -> tuple["hashlib._Hash", int, bool]:
    """Stream-hash the file's already-ingested prefix, count its lines, and detect divergence.

    ``prefix_hash`` is sha256 of the file's bytes ``[0:checkpoint]`` — every line read,
    stored and dedup-skipped alike. sha256 running state can't be restored from a stored
    hexdigest, so on resume we re-hash the prefix from the file in 1 MiB blocks (O(1)
    memory). That same recomputed hash is what tells us whether the file still *is* the file
    we captured: ``diverged`` is True when the file is shorter than ``checkpoint`` (rewritten
    or truncated) or its ``[0:checkpoint]`` bytes no longer hash to ``expected_prefix_hash``.
    The caller handles a True by freezing the old generation and re-ingesting from scratch.

    The returned line count is the number of file lines the prefix contains: its newlines,
    plus one when the prefix ends without one (a captured no-newline final line). It is only
    meaningful when the prefix is intact — a diverged file is re-ingested from offset 0.
    """
    h = hashlib.sha256()
    lines = 0
    last_byte = b"\n"
    diverged = False
    if checkpoint > 0:
        with path.open("rb") as fh:
            remaining = checkpoint
            while remaining > 0:
                block = fh.read(min(1024 * 1024, remaining))
                if not block:
                    diverged = True  # file shorter than checkpoint: it was rewritten/truncated
                    break
                h.update(block)
                lines += block.count(b"\n")
                last_byte = block[-1:]
                remaining -= len(block)
        if last_byte != b"\n":
            lines += 1
        if not diverged and h.hexdigest() != expected_prefix_hash:
            diverged = True  # same length, different bytes: the prefix was rewritten
    return h, lines, diverged


# --- Get-or-create -----------------------------------------------------------------------


def _get_or_create_project(db: Session, slug: str, now) -> Project:
    project = db.query(Project).filter_by(dir_slug=slug).first()
    if project is None:
        project = Project(dir_slug=slug, first_seen_at=now)
        db.add(project)
        db.flush()
    return project


def _get_or_create_session(db: Session, session_uuid: str, project: Project) -> ChatSession:
    session = db.get(ChatSession, session_uuid)
    if session is None:
        session = ChatSession(session_uuid=session_uuid, project_id=project.id)
        db.add(session)
        db.flush()
    return session


def _get_or_create_transcript(db: Session, f: DiscoveredFile, session: ChatSession) -> Transcript:
    # NOTE(claude): a 'backup' file is an older copy of its session's main transcript, so it
    # maps to transcript kind 'main' (agent_hex_id None) — the same identity as the main
    # file. That is what lets a .bak's lines dedup against the main's.
    kind = "subagent" if f.kind == "subagent" else "main"
    transcript = (
        db.query(Transcript)
        .filter_by(session_id=session.session_uuid, kind=kind, agent_hex_id=f.agent_hex_id)
        .first()
    )
    if transcript is None:
        meta = f.agent_meta
        transcript = Transcript(
            session_id=session.session_uuid,
            kind=kind,
            agent_hex_id=f.agent_hex_id,
            agent_type=meta.agent_type if meta else None,
            agent_description=meta.description if meta else None,
            parent_tool_use_id=meta.tool_use_id if meta else None,
        )
        db.add(transcript)
        db.flush()
    return transcript


def _get_or_create_source_file(
    db: Session,
    f: DiscoveredFile,
    project: Project,
    transcript: Transcript,
    size: int,
    now,
) -> SourceFile:
    source_file = (
        db.query(SourceFile)
        .filter_by(path=str(f.path))
        .order_by(SourceFile.generation.desc())  # highest generation is the live one (Task 7)
        .first()
    )
    if source_file is None:
        # A backup is never primary; a non-backup is primary only if its transcript doesn't
        # already have one (guards a restored copy showing up at a second path).
        has_primary = (
            db.query(SourceFile.id)
            .filter_by(transcript_id=transcript.id, is_primary=True)
            .first()
            is not None
        )
        source_file = SourceFile(
            project_id=project.id,
            transcript_id=transcript.id,
            path=str(f.path),
            generation=0,
            kind=f.kind,
            is_primary=(f.kind != "backup" and not has_primary),
            byte_offset_checkpoint=0,
            last_size=size,
            prefix_hash=_EMPTY_SHA256,
            status="active",
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(source_file)
        db.flush()
    else:
        source_file.last_seen_at = now
    return source_file


def _handle_divergence(
    db: Session,
    f: DiscoveredFile,
    old: SourceFile,
    project: Project,
    transcript: Transcript,
    size: int,
    now,
    reappeared: bool = False,
) -> SourceFile:
    """Freeze a rewritten file's old generation and open a fresh one at the same path.

    The old row is marked ``diverged`` and demoted from primary, and a file-level
    ``source_diverged`` error anomaly (``raw_record_id`` NULL, ``source_file_id`` on the old
    row) records the break — with ``reappeared`` true when the old generation had been
    gone_at_source and the path came back holding different bytes. A new SourceFile at the
    same path, ``generation`` + 1, is created and returned for a full re-ingest. Its primary
    status follows the normal rule — primary iff the transcript now has none, which (after
    demoting the old row) it does whenever nothing else already claimed it. Commits before
    returning so the frozen old generation and the new one are durable before re-ingest
    touches a byte.
    """
    old.status = "diverged"
    old.is_primary = False
    db.add(
        ParseAnomaly(
            raw_record_id=None,  # file-level: no single captured line is at fault
            source_file_id=old.id,
            severity="error",
            kind="source_diverged",
            detail={
                "path": str(f.path),
                "old_generation": old.generation,
                "new_generation": old.generation + 1,
                "old_checkpoint": old.byte_offset_checkpoint,
                "old_prefix_hash": old.prefix_hash,
                "new_size": size,
                "reappeared": reappeared,
            },
            schema_version=None,
            created_at=utcnow(),
        )
    )
    db.flush()  # apply the demotion before we ask whether the transcript still has a primary

    # The old generation is now demoted; drop its interpretation rows before the new
    # generation re-ingests the same record_uuids, or the reading room would show two Messages
    # per uuid across generations. Staged here and committed by this function's db.commit().
    interpret.remove_interpretation_for_source_file(db, old.id)

    has_primary = (
        db.query(SourceFile.id)
        .filter_by(transcript_id=transcript.id, is_primary=True)
        .first()
        is not None
    )
    new = SourceFile(
        project_id=project.id,
        transcript_id=transcript.id,
        path=str(f.path),
        generation=old.generation + 1,
        kind=f.kind,
        is_primary=(f.kind != "backup" and not has_primary),
        byte_offset_checkpoint=0,
        last_size=size,
        prefix_hash=_EMPTY_SHA256,
        status="active",
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(new)
    db.commit()
    return new


def _reactivate_source_file(db: Session, source_file: SourceFile) -> None:
    """A gone_at_source path is back on disk with its captured prefix intact: same file.

    The caller has already verified prefix identity (size >= checkpoint AND
    sha256(file[0:checkpoint]) == stored prefix_hash), so this is a clean resume on the SAME
    row — flip it back to ``active``, clear ``gone_detected_at``, and record a file-level
    info ``source_reappeared`` anomaly. Commits so the reactivation is durable before tail
    ingest touches a byte; a fully-live row means a later rewrite fires divergence normally.
    """
    db.add(
        ParseAnomaly(
            raw_record_id=None,  # file-level: nothing about the captured lines changed
            source_file_id=source_file.id,
            severity="info",
            kind="source_reappeared",
            detail={
                "path": source_file.path,
                "generation": source_file.generation,
                "previous_status": source_file.status,
                "gone_detected_at": (
                    source_file.gone_detected_at.isoformat()
                    if source_file.gone_detected_at
                    else None
                ),
            },
            schema_version=None,
            created_at=utcnow(),
        )
    )
    source_file.status = "active"
    source_file.gone_detected_at = None
    db.commit()
