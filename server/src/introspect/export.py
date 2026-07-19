"""Export: byte-faithful reconstruction of a captured transcript.

THE flagship archive guarantee — ``import -> export`` must equal the original file's
bytes, exactly. Capture (see ``ingest/capture.py``) stores each complete line's raw bytes
in ``raw_records``; export is the inverse: concatenate a transcript's stored ``raw_line``
bytes back in ``line_number`` order. Because every byte that was on disk (including the
trailing newline, or its deliberate absence on a torn final line) is in ``raw_line``,
concatenation reproduces the source file with no re-serialization in the loop.

Which source file's bytes do we hand back? A transcript can have several source files
(a main plus its ``.bak``; an old diverged generation plus the live one). Export always
reconstructs the *current* transcript:

* Its **primary** source file — the live generation. After a divergence the new generation
  is primary and byte-complete (capture re-ingested it bypassing dedup), so this is always
  the whole current file, never a sparse graft.
* When **no** source file is primary — e.g. a bak-only transcript, where the only copy that
  ever reached us is a non-primary backup — fall back to the **most-complete** source file
  by stored-record count (Opus review M5). This is the best full reconstruction available.

Diverged OLD generations are intentionally NOT reachable through this API: the signature
carries no generation, so export is always about the live file. (Historical generations
remain in ``raw_records`` for forensic queries; exposing them is a separate concern.)

This module reads models only — it never imports the ingest pipeline. Rows stream from the
query (``yield_per``) so a transcript with very large lines is never fully materialized as
ORM objects; :func:`export_session_to` streams straight to disk without buffering the file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from introspect.models import ChatSession, RawRecord, SourceFile, Transcript

_YIELD_PER = 500


class SessionNotFoundError(LookupError):
    """No session with the requested UUID exists in the archive."""


class TranscriptNotFoundError(LookupError):
    """The session exists but has no transcript of the requested kind/agent."""


def export_transcript(
    db: Session,
    session_uuid: str,
    kind: str = "main",
    agent_hex_id: str | None = None,
) -> bytes:
    """Return the transcript's source file reconstructed byte-for-byte.

    Concatenates ``raw_line`` for the transcript's primary source file (or the
    most-complete one when none is primary) in ``line_number`` order.

    Raises :class:`SessionNotFoundError` if the session is unknown and
    :class:`TranscriptNotFoundError` if the session has no such transcript.
    """
    source_file = _resolve_source_file(db, session_uuid, kind, agent_hex_id)
    buf = bytearray()
    for raw_line in _iter_raw_lines(db, source_file.id):
        buf += raw_line
    return bytes(buf)


def iter_transcript_lines(
    db: Session,
    session_uuid: str,
    kind: str = "main",
    agent_hex_id: str | None = None,
) -> Iterator[bytes]:
    """Yield the transcript's stored ``raw_line`` bytes one line at a time, streaming.

    The generator form of :func:`export_transcript`: concatenating everything it yields equals
    that function's bytes exactly, but nothing is buffered -- each line comes straight off the
    ``yield_per`` query, so the HTTP export endpoint can stream a multi-MB transcript without
    materializing it. Source-file resolution runs before the first yield, so priming the
    generator with one ``next()`` surfaces :class:`SessionNotFoundError` /
    :class:`TranscriptNotFoundError` eagerly -- the endpoint turns them into a 404 before any
    ``200`` body starts. Same not-found contract as :func:`export_transcript`.
    """
    source_file = _resolve_source_file(db, session_uuid, kind, agent_hex_id)
    yield from _iter_raw_lines(db, source_file.id)


def export_session_to(db: Session, session_uuid: str, out_path: Path) -> int:
    """Write the session's ``main`` transcript bytes to ``out_path``; return the byte count.

    Streams each stored line straight to disk (the whole file is never buffered in memory),
    so the return value is the exact number of bytes written — which, by the export
    guarantee, equals the original file's size.
    """
    source_file = _resolve_source_file(db, session_uuid, "main", None)
    total = 0
    with out_path.open("wb") as fh:
        for raw_line in _iter_raw_lines(db, source_file.id):
            fh.write(raw_line)
            total += len(raw_line)
    return total


# --- Resolution ---------------------------------------------------------------------------


def _resolve_source_file(
    db: Session, session_uuid: str, kind: str, agent_hex_id: str | None
) -> SourceFile:
    """Find the source file whose bytes reconstruct this transcript, or raise not-found."""
    if db.get(ChatSession, session_uuid) is None:
        raise SessionNotFoundError(session_uuid)

    transcript = (
        db.query(Transcript)
        .filter_by(session_id=session_uuid, kind=kind, agent_hex_id=agent_hex_id)
        .first()
    )
    if transcript is None:
        raise TranscriptNotFoundError(
            f"session {session_uuid!r} has no {kind!r} transcript (agent_hex_id={agent_hex_id!r})"
        )

    primary = (
        db.query(SourceFile)
        .filter_by(transcript_id=transcript.id, is_primary=True)
        .first()
    )
    if primary is not None:
        return primary

    # M5 fallback: no primary (e.g. bak-only transcript) — reconstruct from the most-complete
    # source file. LEFT JOIN so a source file with zero records still ranks (as 0); ties break
    # on the newest generation, then highest id, for a deterministic choice.
    fallback = (
        db.query(SourceFile)
        .outerjoin(RawRecord, RawRecord.source_file_id == SourceFile.id)
        .filter(SourceFile.transcript_id == transcript.id)
        .group_by(SourceFile.id)
        .order_by(
            func.count(RawRecord.id).desc(),
            SourceFile.generation.desc(),
            SourceFile.id.desc(),
        )
        .first()
    )
    if fallback is None:
        # The transcript row exists but no source file was ever attached to it — nothing to
        # reconstruct. Treat as not found rather than returning silently-empty bytes.
        raise TranscriptNotFoundError(
            f"transcript for session {session_uuid!r} ({kind!r}) has no source file to export"
        )
    return fallback


def _iter_raw_lines(db: Session, source_file_id: int) -> Iterator[bytes]:
    """Yield a source file's stored ``raw_line`` bytes in ``line_number`` order, streaming.

    Selects the single ``raw_line`` column (not whole ORM entities) with ``yield_per`` so the
    528 KB lines that exist in real transcripts are handled one at a time, never all at once.
    """
    query = (
        db.query(RawRecord.raw_line)
        .filter(RawRecord.source_file_id == source_file_id)
        .order_by(RawRecord.line_number)
        .yield_per(_YIELD_PER)
    )
    for (raw_line,) in query:
        yield raw_line
