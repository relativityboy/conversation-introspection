"""Resume endpoint (spec §17.2): ``POST /api/v1/sessions/{uuid}/resume``.

POST, not PUT — each call may spawn a terminal window; it is not idempotent state. HTTP errors
only when we can't even try: 404 unknown AND 404 archived (§15.1 — archived sessions are
indistinguishable from nonexistent on every API path; direct probe, admin-export precedent).
Everything downstream — missing cwd, `open` failure, non-darwin — is a 200 with an honest
``mode`` (§17.3): a failed launch after a successful restore is an outcome to report, not an
exception. The subprocess edge is injectable via ``app.state.resume_runner`` (None in
production → real subprocess), the CrontabIO seam adapted to FastAPI state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import ResumeResult
from introspect.models import ArchivedSession, ChatSession
from introspect.resume import resume_session

router = APIRouter(prefix="/api/v1")


@router.post("/sessions/{session_uuid}/resume", response_model=ResumeResult)
def resume_session_endpoint(
    session_uuid: str, request: Request, db: Session = Depends(get_db)
) -> ResumeResult:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")
    if db.get(ArchivedSession, session_uuid) is not None:
        raise LookupError(f"session {session_uuid} not found")
    outcome = resume_session(
        db,
        session_uuid,
        source_root=request.app.state.source_root,
        terminal_app=request.app.state.terminal_app,
        scripts_dir=request.app.state.db_path.parent / "resume-scripts",
        runner=request.app.state.resume_runner,
    )
    return ResumeResult(
        restored=outcome.restored,
        launched=outcome.launched,
        mode=outcome.mode,
        command=outcome.command,
        cwd=outcome.cwd,
        live_path=outcome.live_path,
        detail=outcome.detail,
    )
