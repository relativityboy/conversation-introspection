"""User title endpoint (Task P4-1): ``PUT /api/v1/sessions/{uuid}/title``.

A user title is existence-based, not a nullable column on ``sessions`` (see ``UserTitle`` in
:mod:`introspect.models`): a session has a user title iff a ``user_titles`` row with its
``session_uuid`` exists. There is only one verb -- ``PUT`` -- because "unset" is expressed by
PUTting an empty/whitespace title rather than a separate ``DELETE``: sending back the
archive-derived title (``ai_title``/``custom_title``) IS the delete. Both the upsert and the
delete branches are idempotent (a second identical PUT updates ``updated_at`` but stays one
row; PUTting empty against an absent row deletes nothing), and both return a bare 204 with no
body either way.

The 200-char cap (spec Sec.14.3 critique #10) validates the RAW title string -- stripping is
used ONLY to decide whether the title counts as empty (and therefore a delete), never to
shrink what gets measured against the cap or what gets stored. This is why the empty-check
runs strictly before the length check: a long whitespace-only title is still a delete, not a
422.

User titles are read-model-adjacent state, never touched by import/reparse: neither
``run_import`` nor ``reparse_all`` writes to or deletes from ``user_titles`` (see
``introspect.ingest.reparse._delete_all_interpretation_rows``, which enumerates exactly the
tables reparse wipes and ``user_titles`` is not among them) -- spec Sec.4's "never touched"
invariant, proven end-to-end by ``test_user_title_survives_import_and_reparse``.

``?import_if_missing=true`` (2026-08-23, the ``/session-name`` skill): the caller is usually the
running agent naming ITS OWN session, which the hourly import has not captured yet. With the
flag, an unknown session triggers one import in-request (same ``run_import`` cron uses, so the
advisory lock is honoured -- a running import is waited for, bounded by
``_IMPORT_WAIT_SECONDS``) and the lookup is retried before the 404 is final. The import runs
only for a real rename: an over-long title is a 422 and an empty one a delete, neither worth
capturing for. The 404 that survives the import names the run and the likely reasons (excluded
project, transcript not yet flushed) because to the agent "not found" alone reads as "import
failed".
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.errors import Problem
from introspect.ingest.run import ImportSummary, run_import
from introspect.models import ChatSession, UserTitle

router = APIRouter(prefix="/api/v1")

_MAX_TITLE_LENGTH = 200
# How long ``import_if_missing`` waits on a running import before giving up with 409. The
# hourly cron import takes seconds; this only has to outlast one of those.
_IMPORT_WAIT_SECONDS = 30.0
_IMPORT_POLL_SECONDS = 0.25


class TitleIn(BaseModel):
    title: str


def _require_session(db: Session, session_uuid: str) -> None:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")


class _ImportBusy(Exception):
    """The advisory lock stayed held past ``_IMPORT_WAIT_SECONDS``."""


def _import_waiting_for_lock(db_path: Path, source_root: Path) -> ImportSummary:
    """Run one import, waiting (bounded) while another run holds the advisory lock."""
    deadline = time.monotonic() + _IMPORT_WAIT_SECONDS
    while True:
        summary = run_import(db_path, source_root, trigger="api")
        if summary.status != "already_running" or time.monotonic() >= deadline:
            return summary
        time.sleep(_IMPORT_POLL_SECONDS)


def _capture_then_require_session(request: Request, db: Session, session_uuid: str) -> None:
    # NOTE(claude): the request session already opened a read transaction for the first
    # lookup. Under WAL that snapshot would never see rows the import commits, so end it
    # before importing -- the retry below must read post-import state.
    db.rollback()
    summary = _import_waiting_for_lock(request.app.state.db_path, request.app.state.source_root)
    if summary.status == "already_running":
        raise _ImportBusy
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(
            f"session {session_uuid} not found after import run {summary.run_id} "
            f"({summary.status}): its project may be excluded from capture, or its "
            "transcript has not been written to disk yet"
        )


@router.put(
    "/sessions/{session_uuid}/title",
    status_code=204,
    responses={409: {"model": Problem, "description": "import already running"}},
)
def set_title(
    session_uuid: str,
    body: TitleIn,
    request: Request,
    db: Session = Depends(get_db),
    import_if_missing: bool = False,
) -> Response:
    if not body.title.strip():
        _require_session(db, session_uuid)
        existing = db.get(UserTitle, session_uuid)
        if existing is not None:
            db.delete(existing)
            db.commit()
        return Response(status_code=204)

    if len(body.title) > _MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"title must be at most {_MAX_TITLE_LENGTH} characters"
        )

    if db.get(ChatSession, session_uuid) is None:
        if not import_if_missing:
            raise LookupError(f"session {session_uuid} not found")
        try:
            _capture_then_require_session(request, db, session_uuid)
        except _ImportBusy:
            # Same Problem shape POST /import returns for the contended lock (see admin.py).
            problem = Problem(
                status=409,
                title="import already running",
                detail=(
                    "an import run held the advisory lock for longer than "
                    f"{_IMPORT_WAIT_SECONDS:g}s; retry shortly"
                ),
            )
            return JSONResponse(status_code=409, content=problem.model_dump())

    existing = db.get(UserTitle, session_uuid)
    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(UserTitle(session_uuid=session_uuid, title=body.title, updated_at=now))
    else:
        existing.title = body.title
        existing.updated_at = now
    db.commit()
    return Response(status_code=204)
