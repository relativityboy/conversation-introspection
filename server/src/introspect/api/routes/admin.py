"""Admin endpoints (Task P2-8): import trigger, run history, status, anomalies, export.

Everything here is read-only EXCEPT ``POST /import``, which triggers a background import. That
handler is the one place in the API that writes ingest state, and it does so carefully:

* It takes a NON-BLOCKING probe of the same advisory lock ``run_import`` uses. If the lock is
  already held, it refuses with a 409 problem and writes no row -- the obvious concurrent case
  fails fast. Otherwise it CREATES the ``ImportRun`` row (``trigger='api'``, ``status='running'``)
  via the request session, commits, RELEASES the probe, and only then starts the worker thread.
* The worker runs :func:`introspect.ingest.run.run_import` with ``run_id=<id>`` against its own
  engine (thread-safe by design) and finalizes that pre-created row. The probe is released
  before the thread starts so the thread's own ``run_import`` can take the real lock; the small
  window between release and re-acquire is the benign race ``run_import`` handles by finalizing
  the pre-created row ``'errors'`` (never stranding it ``'running'``).

The started ``Thread`` is stored on ``app.state.last_import_thread`` so callers (and tests) can
join it -- the tmp DB must not be deleted out from under a live writer.

The export endpoint streams a transcript's bytes over a GENERATOR (never buffered): see
:func:`export_transcript_jsonl` for how the streaming session's lifetime is handled.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from introspect import export
from introspect.api.deps import get_db
from introspect.api.models import _DEFAULT_LIMIT, _MAX_LIMIT, Problem
from introspect.export import SessionNotFoundError, TranscriptNotFoundError

from introspect.db import get_engine, session_factory

# NOTE(claude): `_acquire_lock`/`_release_lock` are private to run.py, but the API needs the
# SAME advisory lock as run_import to probe for a concurrent import. Reusing them (as cli.py
# already does for reparse) is a smaller diff than lifting them to a shared module.
from introspect.ingest.run import DbOpenError, _acquire_lock, _release_lock, run_import
from introspect.models import ChatSession, ImportRun, ParseAnomaly, RawRecord, SourceFile

router = APIRouter(prefix="/api/v1")


# --- Response models --------------------------------------------------------------------


class ImportRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    files_seen: int
    records_added: int
    records_skipped_duplicate: int
    anomaly_count: int


class ImportRunList(BaseModel):
    items: list[ImportRunOut]
    total: int


class TriggerImportOut(BaseModel):
    run_id: int


class AnomalyOut(BaseModel):
    id: int
    severity: str
    kind: str
    detail: dict
    source_file_path: str | None
    created_at: datetime


class AnomalyList(BaseModel):
    items: list[AnomalyOut]
    total: int


class AnomalyBreakdown(BaseModel):
    error: int
    warn: int
    info: int


class StatusOut(BaseModel):
    sessions: int
    files: int
    records: int
    archive_bytes: int
    anomalies: AnomalyBreakdown
    last_run: ImportRunOut | None


# --- Import trigger ---------------------------------------------------------------------


@router.post(
    "/import",
    status_code=202,
    response_model=TriggerImportOut,
    responses={409: {"model": Problem, "description": "import already running"}},
)
def trigger_import(request: Request, db: Session = Depends(get_db)):
    db_path: Path = request.app.state.db_path
    source_root: Path = request.app.state.source_root

    lock_fh = _acquire_lock(db_path.parent / "import.lock")
    if lock_fh is None:
        # NOTE(claude): the central error handler derives the problem title from the status
        # phrase ("Conflict"); the brief pins this title to "import already running", so this
        # one response is shaped directly from the same Problem model instead.
        problem = Problem(
            status=409,
            title="import already running",
            detail="an import run already holds the advisory lock",
        )
        return JSONResponse(status_code=409, content=problem.model_dump())

    try:
        run = ImportRun(
            trigger="api", status="running", started_at=datetime.now(timezone.utc)
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        # Release the probe BEFORE starting the worker: the worker's own run_import must be
        # able to take the real lock. The window here is the benign race run_import handles.
        _release_lock(lock_fh)

    thread = threading.Thread(
        target=_run_import_in_thread,
        args=(db_path, source_root, run_id),
        daemon=True,
    )
    thread.start()
    request.app.state.last_import_thread = thread
    return TriggerImportOut(run_id=run_id)


def _run_import_in_thread(db_path: Path, source_root: Path, run_id: int) -> None:
    """Worker-thread body: run the import, and never strand the pre-created row on DbOpenError.

    ``run_import`` finalizes the pre-created row itself on every path it can reach the DB from
    (ok/errors, mid-run fatal, lost-lock race) -- but :class:`DbOpenError` is raised BEFORE any
    of those, so the handler's ``'running'`` row would be stranded. Make ONE best-effort attempt
    to finalize it ``'fatal'``; if that also fails the DB is truly unopenable and the thread
    exits silently -- unrecoverable by nature (documented as an accepted residual in run.py's
    module docstring, alongside SIGKILL).
    """
    try:
        run_import(db_path, source_root, trigger="api", run_id=run_id)
    except DbOpenError:
        _finalize_unopenable(db_path, run_id)


def _finalize_unopenable(db_path: Path, run_id: int) -> None:
    """Best-effort 'fatal' finalize after run_import could not open/migrate the DB. Never raises.

    Opens a fresh engine/session purely to stamp the pre-created row (status='fatal',
    finished_at=now) and closes it. The open that just failed inside run_import was
    open+MIGRATE; a plain open for one UPDATE can still succeed (e.g. a transient migration
    failure), so this attempt is worth making -- and if it fails too, swallowing is correct:
    with the DB down there is nowhere to record anything.
    """
    try:
        engine = get_engine(db_path)
        with session_factory(engine)() as db:
            run = db.get(ImportRun, run_id)
            if run is None:
                return
            run.status = "fatal"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:  # noqa: BLE001, S110 -- DB truly unopenable; nowhere to record anything
        pass


# --- Run history ------------------------------------------------------------------------


@router.get("/import/runs", response_model=ImportRunList)
def list_import_runs(
    db: Session = Depends(get_db), limit: int = _DEFAULT_LIMIT, offset: int = 0
) -> ImportRunList:
    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)
    total = db.scalar(select(func.count(ImportRun.id)))
    rows = (
        db.execute(
            select(ImportRun).order_by(ImportRun.id.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return ImportRunList(
        items=[ImportRunOut.model_validate(r) for r in rows], total=total or 0
    )


@router.get("/import/runs/{run_id}", response_model=ImportRunOut)
def get_import_run(run_id: int, db: Session = Depends(get_db)) -> ImportRunOut:
    run = db.get(ImportRun, run_id)
    if run is None:
        raise LookupError(f"import run {run_id} not found")
    return ImportRunOut.model_validate(run)


# --- Status -----------------------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
def get_status(request: Request, db: Session = Depends(get_db)) -> StatusOut:
    sessions = db.scalar(select(func.count(ChatSession.session_uuid))) or 0
    files = db.scalar(select(func.count(SourceFile.id))) or 0
    records = db.scalar(select(func.count(RawRecord.id))) or 0

    def _severity_count(severity: str) -> int:
        return (
            db.scalar(
                select(func.count(ParseAnomaly.id)).where(
                    ParseAnomaly.severity == severity
                )
            )
            or 0
        )

    last_run = (
        db.execute(select(ImportRun).order_by(ImportRun.id.desc()).limit(1))
        .scalars()
        .first()
    )

    db_path: Path = request.app.state.db_path
    archive_bytes = os.stat(db_path).st_size

    return StatusOut(
        sessions=sessions,
        files=files,
        records=records,
        archive_bytes=archive_bytes,
        anomalies=AnomalyBreakdown(
            error=_severity_count("error"),
            warn=_severity_count("warn"),
            info=_severity_count("info"),
        ),
        last_run=ImportRunOut.model_validate(last_run) if last_run is not None else None,
    )


# --- Anomalies --------------------------------------------------------------------------


@router.get("/anomalies", response_model=AnomalyList)
def list_anomalies(
    db: Session = Depends(get_db),
    severity: Literal["info", "warn", "error"] | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> AnomalyList:
    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)

    stmt = select(ParseAnomaly, SourceFile.path).outerjoin(
        SourceFile, ParseAnomaly.source_file_id == SourceFile.id
    )
    if severity is not None:
        stmt = stmt.where(ParseAnomaly.severity == severity)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    rows = db.execute(
        stmt.order_by(ParseAnomaly.id.desc()).limit(limit).offset(offset)
    ).all()
    items = [
        AnomalyOut(
            id=anomaly.id,
            severity=anomaly.severity,
            kind=anomaly.kind,
            detail=anomaly.detail,
            source_file_path=path,
            created_at=anomaly.created_at,
        )
        for (anomaly, path) in rows
    ]
    return AnomalyList(items=items, total=total or 0)


# --- Export -----------------------------------------------------------------------------


@router.get("/sessions/{session_uuid}/export.jsonl")
def export_transcript_jsonl(session_uuid: str, request: Request) -> StreamingResponse:
    """Stream a session's main transcript back byte-for-byte as ``application/x-ndjson``.

    Streaming-generator session lifetime: the request-scoped ``get_db`` session would close
    when this handler returns, but ``StreamingResponse`` consumes the body AFTER return -- a
    generator reading through a closed session would break. So the stream OWNS a dedicated
    session opened from ``app.state.session_factory`` and closed in the generator's ``finally``
    (materializing the transcript into a per-line list is forbidden -- that defeats streaming a
    large transcript). We prime the first line synchronously here so an unknown session becomes
    a clean 404 problem BEFORE any ``200`` body starts; the remaining lines stream lazily.
    """
    stream_db: Session = request.app.state.session_factory()
    lines = export.iter_transcript_lines(stream_db, session_uuid)
    try:
        # NOTE(claude): priming holds ONE line in memory (real transcripts have lines up to
        # ~528 KB) before streaming begins. Accepted tradeoff: it is the only way to turn an
        # unknown session into a clean 404 problem instead of a 200 that dies mid-body, and one
        # line is bounded -- the forbidden case (the whole transcript buffered) stays forbidden.
        first = next(lines)  # runs source-file resolution; raises on unknown session/transcript
    except StopIteration:
        first = None  # resolved fine, but the transcript has no lines
    except (SessionNotFoundError, TranscriptNotFoundError) as exc:
        stream_db.close()
        raise LookupError(str(exc)) from exc

    def body() -> Iterator[bytes]:
        try:
            if first is not None:
                yield first
            yield from lines
        finally:
            stream_db.close()

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{session_uuid}.jsonl"'
        },
    )
