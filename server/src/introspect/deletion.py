"""Reasoned, ceremonied deletion (spec 2026-08-17 §3). Owner-only: reachable from the TUI
and CLI, NEVER from the API — no agent-facing surface can remove memory.

Cascade order is load-bearing: content is de-indexed through the search index's
external-content ``'delete'`` command BEFORE its rows vanish (the migration 0002 trap:
FTS5 keeps no text copy, so de-indexing needs the original rows still present; skipping
this corrupts the database file). Every deletion writes a :class:`DeletionLedger` row —
the archive remembers THAT it forgot, never WHAT. The ledger records; it never blocks
(the excluded_sessions wall does that, separately and only by explicit consent).

Backups are NEVER touched by the main deletion: :func:`scrub_backups` is a distinct,
additionally-consented act (relativityboy 2026-08-17: "additional-ask with yes/no").
"""

from __future__ import annotations

import os
import stat as stat_mod
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.models import (
    ArchivedSession,
    ChatSession,
    ContentBlock,
    DeletionLedger,
    ExcludedSession,
    Favorite,
    Message,
    ParseAnomaly,
    Project,
    RawRecord,
    SessionEvent,
    SourceFile,
    TokenUsage,
    Transcript,
    UserTitle,
)
from introspect.search import get_search_index

#: The mandatory ceremony warning (spec §3). Keep alarming and explicit.
DELETION_WARNING = (
    "WARNING: deletion is IRREVERSIBLE. The archived bytes, interpretation, search index "
    "entries, titles, and favorites for this target will be permanently destroyed. The "
    "deletion ledger will record that this happened (target, time, your reason) but never "
    "the content."
)


@dataclass(frozen=True)
class Preview:
    kind: str  # 'session' | 'project'
    target: str
    label: str | None
    sessions: int
    records: int
    #: Live source files still on disk — the resurrection risk: the next import re-captures
    #: them unless re-import is forbidden (session wall) or the project is excluded.
    source_paths_on_disk: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    kind: str
    target: str
    label: str | None
    sessions_deleted: int
    records_deleted: int
    source_paths_on_disk: tuple[str, ...]


def _session_label(db: Session, session: ChatSession) -> str:
    user_title = db.query(UserTitle).filter_by(session_uuid=session.session_uuid).one_or_none()
    for candidate in (
        user_title.title if user_title else None,
        session.ai_title,
        session.custom_title,
    ):
        if candidate and candidate.strip():
            return candidate
    return session.session_uuid[:8]


def _scope(db: Session, session_uuid: str):
    """All row ids belonging to one session, across the cascade tables."""
    t_ids = [t.id for t in db.query(Transcript).filter_by(session_id=session_uuid)]
    m_ids = (
        [m.id for m in db.query(Message.id).filter(Message.transcript_id.in_(t_ids))]
        if t_ids
        else []
    )
    rr_ids = (
        [r.id for r in db.query(RawRecord.id).filter(RawRecord.transcript_id.in_(t_ids))]
        if t_ids
        else []
    )
    sf_ids = (
        [s.id for s in db.query(SourceFile.id).filter(SourceFile.transcript_id.in_(t_ids))]
        if t_ids
        else []
    )
    b_ids = (
        [b.id for b in db.query(ContentBlock.id).filter(ContentBlock.message_id.in_(m_ids))]
        if m_ids
        else []
    )
    return t_ids, m_ids, rr_ids, sf_ids, b_ids


def _live_source_paths(db: Session, sf_ids: list[int]) -> tuple[str, ...]:
    if not sf_ids:
        return ()
    paths = [
        sf.path for sf in db.query(SourceFile).filter(SourceFile.id.in_(sf_ids))
    ]
    return tuple(p for p in paths if Path(p).exists())


def _delete_session_rows(db: Session, session_uuid: str) -> tuple[int, tuple[str, ...]]:
    """The cascade for ONE session (no ledger, no commit). Returns (raw_record_count,
    still-on-disk source paths). Caller wraps with ledger + commit."""
    t_ids, m_ids, rr_ids, sf_ids, b_ids = _scope(db, session_uuid)
    on_disk = _live_source_paths(db, sf_ids)

    # 1 — de-index BEFORE deleting rows (external-content trap; re-reads original text).
    get_search_index().delete_for_blocks(db, b_ids)

    # 2 — interpretation rows, leaves before parents.
    if m_ids:
        db.query(TokenUsage).filter(TokenUsage.message_id.in_(m_ids)).delete(
            synchronize_session=False
        )
    if b_ids:
        db.query(ContentBlock).filter(ContentBlock.id.in_(b_ids)).delete(
            synchronize_session=False
        )
    if rr_ids:
        db.query(SessionEvent).filter(SessionEvent.raw_record_id.in_(rr_ids)).delete(
            synchronize_session=False
        )
    if m_ids:
        db.query(Message).filter(Message.id.in_(m_ids)).delete(synchronize_session=False)

    # 3 — archive rows (anomalies reference raw records AND source files, both nullable).
    if rr_ids or sf_ids:
        db.query(ParseAnomaly).filter(
            (ParseAnomaly.raw_record_id.in_(rr_ids or [-1]))
            | (ParseAnomaly.source_file_id.in_(sf_ids or [-1]))
        ).delete(synchronize_session=False)
    if rr_ids:
        db.query(RawRecord).filter(RawRecord.id.in_(rr_ids)).delete(
            synchronize_session=False
        )
    if sf_ids:
        db.query(SourceFile).filter(SourceFile.id.in_(sf_ids)).delete(
            synchronize_session=False
        )

    # 4 — user rows, then identity.
    for model in (Favorite, UserTitle, ArchivedSession):
        db.query(model).filter_by(session_uuid=session_uuid).delete(
            synchronize_session=False
        )
    if t_ids:
        db.query(Transcript).filter(Transcript.id.in_(t_ids)).delete(
            synchronize_session=False
        )
    db.query(ChatSession).filter_by(session_uuid=session_uuid).delete(
        synchronize_session=False
    )
    return len(rr_ids), on_disk


def preview_session(db: Session, session_uuid: str) -> Preview | None:
    session = db.get(ChatSession, session_uuid)
    if session is None:
        return None
    _, _, rr_ids, sf_ids, _ = _scope(db, session_uuid)
    return Preview(
        kind="session",
        target=session_uuid,
        label=_session_label(db, session),
        sessions=1,
        records=len(rr_ids),
        source_paths_on_disk=_live_source_paths(db, sf_ids),
    )


def preview_project(db: Session, dir_slug: str) -> Preview | None:
    project = db.query(Project).filter_by(dir_slug=dir_slug).one_or_none()
    if project is None:
        return None
    uuids = [
        s.session_uuid for s in db.query(ChatSession).filter_by(project_id=project.id)
    ]
    records = 0
    on_disk: list[str] = []
    for uuid in uuids:
        _, _, rr_ids, sf_ids, _ = _scope(db, uuid)
        records += len(rr_ids)
        on_disk.extend(_live_source_paths(db, sf_ids))
    return Preview(
        kind="project",
        target=dir_slug,
        label=dir_slug,
        sessions=len(uuids),
        records=records,
        source_paths_on_disk=tuple(on_disk),
    )


def delete_session(db: Session, session_uuid: str, reason: str | None) -> Outcome | None:
    from introspect.ingest.capture import utcnow

    session = db.get(ChatSession, session_uuid)
    if session is None:
        return None
    label = _session_label(db, session)
    records, on_disk = _delete_session_rows(db, session_uuid)
    db.add(
        DeletionLedger(
            kind="session",
            target=session_uuid,
            label=label,
            reason=reason,
            sessions_deleted=1,
            records_deleted=records,
            created_at=utcnow(),
        )
    )
    db.commit()
    return Outcome(
        kind="session",
        target=session_uuid,
        label=label,
        sessions_deleted=1,
        records_deleted=records,
        source_paths_on_disk=on_disk,
    )


def delete_project(db: Session, dir_slug: str, reason: str | None) -> Outcome | None:
    from introspect.ingest.capture import utcnow

    project = db.query(Project).filter_by(dir_slug=dir_slug).one_or_none()
    if project is None:
        return None
    uuids = [
        s.session_uuid for s in db.query(ChatSession).filter_by(project_id=project.id)
    ]
    records = 0
    on_disk: list[str] = []
    for uuid in uuids:
        n, paths = _delete_session_rows(db, uuid)
        records += n
        on_disk.extend(paths)
    db.query(Project).filter_by(id=project.id).delete(synchronize_session=False)
    db.add(
        DeletionLedger(
            kind="project",
            target=dir_slug,
            label=dir_slug,
            reason=reason,
            sessions_deleted=len(uuids),
            records_deleted=records,
            created_at=utcnow(),
        )
    )
    db.commit()
    return Outcome(
        kind="project",
        target=dir_slug,
        label=dir_slug,
        sessions_deleted=len(uuids),
        records_deleted=records,
        source_paths_on_disk=tuple(on_disk),
    )


def forbid_reimport(db: Session, session_uuid: str, reason: str | None) -> bool:
    """Add a session to the re-import wall (the ceremony's forbid ask). True if added,
    False if it was already forbidden. Consent lives with the caller — never automatic."""
    from introspect.ingest.capture import utcnow

    if db.get(ExcludedSession, session_uuid) is not None:
        return False
    db.add(ExcludedSession(session_uuid=session_uuid, reason=reason, created_at=utcnow()))
    db.commit()
    return True


# --- Backups: a separate, additionally-consented act --------------------------------------


def list_backup_dbs(db_path: Path) -> list[Path]:
    """DB copies under ``<db_dir>/backups/`` (the 'just make a copy' convention)."""
    backups_dir = Path(db_path).parent / "backups"
    if not backups_dir.is_dir():
        return []
    return sorted(p for p in backups_dir.iterdir() if p.suffix == ".db" and p.is_file())


def scrub_backups(
    db_path: Path, kind: str, target: str, reason: str | None
) -> list[tuple[Path, bool]]:
    """Run the same cascade + ledger inside every backup DB copy. Triple-consented path
    only (spec §3): temporarily lifts and restores the ``uchg`` immutability flag where
    set — consciously superseding the 2026-08-08 "copy stays unaltered" ruling, under
    this explicit ask alone. Returns per-backup (path, target_was_present)."""
    results: list[tuple[Path, bool]] = []
    for bpath in list_backup_dbs(Path(db_path)):
        flags = os.stat(bpath).st_flags if hasattr(os, "chflags") else 0
        immutable = bool(flags & stat_mod.UF_IMMUTABLE)
        if immutable:
            os.chflags(bpath, flags & ~stat_mod.UF_IMMUTABLE)
        try:
            engine = get_engine(bpath)
            upgrade_to_head(engine)  # older copies may predate the ledger tables
            with session_factory(engine)() as bdb:
                if kind == "session":
                    outcome = delete_session(bdb, target, reason)
                else:
                    outcome = delete_project(bdb, target, reason)
            engine.dispose()  # release the file before restoring immutability
            results.append((bpath, outcome is not None))
        finally:
            if immutable:
                os.chflags(bpath, os.stat(bpath).st_flags | stat_mod.UF_IMMUTABLE)
    return results


def finalize_scrub(engine) -> None:  # noqa: ANN001 -- sqlalchemy Engine
    """Make deleted pages actually unrecoverable: checkpoint the WAL and VACUUM.

    Must run OUTSIDE a transaction (SQLite refuses VACUUM inside one), hence the
    autocommit connection. On a large archive VACUUM takes real time — callers should
    say so before invoking."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.exec_driver_sql("VACUUM")
