"""Archive status snapshot, shared by ``introspect status`` (CLI) and the TUI ``/status``.

The counts and their exact one-line formats live here so the CLI and the TUI report identical
numbers from a single source of truth (the TUI additionally appends its web-server state). The
CLI's historical output format is preserved verbatim by :func:`counts_line` /
:func:`last_run_line` -- the existing CLI tests assert on those strings.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from introspect.models import (
    ArchivedSession,
    ChatSession,
    ImportRun,
    ParseAnomaly,
    RawRecord,
    SourceFile,
)


@dataclass
class StatusSnapshot:
    sessions: int
    archived: int
    files: int
    records: int
    anomalies: int
    errors: int
    warns: int
    infos: int
    last_run: ImportRun | None


def collect_status(db: Session) -> StatusSnapshot:
    """Read the archive's headline counts + most recent import run.

    ``archived`` is an aggregate count only (§15.1: "n only, no identities") -- neither this
    snapshot nor its callers ever enumerate which sessions are archived.
    """
    return StatusSnapshot(
        sessions=db.query(ChatSession).count(),
        archived=db.query(ArchivedSession).count(),
        files=db.query(SourceFile).count(),
        records=db.query(RawRecord).count(),
        anomalies=db.query(ParseAnomaly).count(),
        errors=db.query(ParseAnomaly).filter_by(severity="error").count(),
        warns=db.query(ParseAnomaly).filter_by(severity="warn").count(),
        infos=db.query(ParseAnomaly).filter_by(severity="info").count(),
        last_run=db.query(ImportRun).order_by(ImportRun.id.desc()).first(),
    )


def counts_line(snap: StatusSnapshot) -> str:
    return (
        f"sessions={snap.sessions} archived={snap.archived} files={snap.files} "
        f"records={snap.records} anomalies={snap.anomalies} "
        f"(error={snap.errors} warn={snap.warns} info={snap.infos})"
    )


def last_run_line(snap: StatusSnapshot) -> str:
    if snap.last_run is None:
        return "last run: none"
    return (
        f"last run: id={snap.last_run.id} trigger={snap.last_run.trigger} "
        f"status={snap.last_run.status} finished_at={snap.last_run.finished_at}"
    )
