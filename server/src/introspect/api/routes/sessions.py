"""Read endpoints for projects, sessions, and transcript messages (Task P2-5).

Every handler is read-only: it opens a request-scoped :class:`~sqlalchemy.orm.Session` via
:func:`introspect.api.deps.get_db` and constructs the pinned response models from
:mod:`introspect.api.models` (favorites are written by Task 7, never here). Two shape rules
are load-bearing and enforced by the spec:

* Sessions are ordered ``last_activity_at DESC NULLS LAST``. We express NULLS-LAST with an
  ``is_(None)`` sort key ahead of the DESC key rather than ``nullslast()`` so the ordering is
  identical on every SQLite build regardless of its ``NULLS LAST`` support.
* Transcript messages are ordered by ``Message.id`` ALONE. ``timestamp`` is nullable, so a
  timestamp sort would put NULL rows in an arbitrary spot and corrupt the ``around`` ordinal
  math; id order equals insertion order equals file order within a transcript (Opus m4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import (
    BlockOut,
    MessageOut,
    SessionDetail,
    SessionSummary,
    TranscriptInfo,
)
from introspect.models import ChatSession, ContentBlock, Favorite, Message, Project, Transcript

router = APIRouter(prefix="/api/v1")

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# --- Response envelopes (route-local; the item models live in api.models) ---------------


class ProjectOut(BaseModel):
    id: int
    dir_slug: str
    resolved_cwd: str | None
    session_count: int


class SessionList(BaseModel):
    items: list[SessionSummary]
    total: int


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int
    offset: int


# --- Correlated scalar subqueries reused across the session queries ----------------------


def _main_message_count():
    """COUNT of messages in a session's MAIN transcript, correlated to ``ChatSession``."""
    return (
        select(func.count(Message.id))
        .select_from(Message)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(
            Transcript.session_id == ChatSession.session_uuid,
            Transcript.kind == "main",
        )
        .correlate(ChatSession)
        .scalar_subquery()
    )


def _is_favorited():
    """1 if a favorites row exists for the session, else 0; correlated to ``ChatSession``."""
    return (
        select(func.count(Favorite.session_uuid))
        .where(Favorite.session_uuid == ChatSession.session_uuid)
        .correlate(ChatSession)
        .scalar_subquery()
    )


def _summary(session: ChatSession, project_slug: str, message_count: int, favorite: int) -> SessionSummary:
    return SessionSummary(
        session_uuid=session.session_uuid,
        project_slug=project_slug,
        ai_title=session.ai_title,
        custom_title=session.custom_title,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        message_count=message_count,
        favorite=bool(favorite),
    )


# --- Endpoints --------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    rows = (
        db.execute(
            select(
                Project.id,
                Project.dir_slug,
                Project.resolved_cwd,
                func.count(ChatSession.session_uuid),
            )
            .outerjoin(ChatSession, ChatSession.project_id == Project.id)
            .group_by(Project.id)
            .order_by(Project.id)
        )
        .all()
    )
    return [
        ProjectOut(id=pid, dir_slug=slug, resolved_cwd=cwd, session_count=count)
        for (pid, slug, cwd, count) in rows
    ]


@router.get("/sessions", response_model=SessionList)
def list_sessions(
    db: Session = Depends(get_db),
    title: str | None = None,
    favorite: bool | None = None,
    project: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> SessionList:
    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)

    message_count = _main_message_count()
    favorited = _is_favorited()

    stmt = select(
        ChatSession, Project.dir_slug, message_count, favorited
    ).join(Project, ChatSession.project_id == Project.id)

    if title:
        needle = f"%{title.lower()}%"
        stmt = stmt.where(
            func.lower(ChatSession.ai_title).like(needle)
            | func.lower(ChatSession.custom_title).like(needle)
        )
    if favorite:
        stmt = stmt.where(favorited > 0)
    if project:
        stmt = stmt.where(Project.dir_slug == project)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(
        ChatSession.last_activity_at.is_(None),  # non-NULL first -> NULLS LAST under DESC
        ChatSession.last_activity_at.desc(),
        ChatSession.session_uuid,  # deterministic tiebreaker for equal timestamps
    ).limit(limit).offset(offset)

    items = [
        _summary(session, slug, count, fav)
        for (session, slug, count, fav) in db.execute(stmt).all()
    ]
    return SessionList(items=items, total=total or 0)


@router.get("/sessions/{session_uuid}", response_model=SessionDetail)
def get_session(session_uuid: str, db: Session = Depends(get_db)) -> SessionDetail:
    row = db.execute(
        select(ChatSession, Project.dir_slug, _main_message_count(), _is_favorited())
        .join(Project, ChatSession.project_id == Project.id)
        .where(ChatSession.session_uuid == session_uuid)
    ).one_or_none()
    if row is None:
        raise LookupError(f"session {session_uuid} not found")

    session, slug, count, fav = row
    transcripts = db.execute(
        select(Transcript)
        .where(Transcript.session_id == session_uuid)
        .order_by(Transcript.kind, Transcript.id)  # 'main' before 'subagent'
    ).scalars().all()

    summary = _summary(session, slug, count, fav)
    return SessionDetail(
        **summary.model_dump(),
        transcripts=[TranscriptInfo.model_validate(t) for t in transcripts],
    )


@router.get("/transcripts/{transcript_id}/messages", response_model=MessageList)
def list_messages(
    transcript_id: int,
    db: Session = Depends(get_db),
    offset: int = 0,
    limit: int = _DEFAULT_LIMIT,
    around: str | None = None,
) -> MessageList:
    if db.get(Transcript, transcript_id) is None:
        raise LookupError(f"transcript {transcript_id} not found")

    limit = min(max(limit, 1), _MAX_LIMIT)

    total = db.scalar(
        select(func.count(Message.id)).where(Message.transcript_id == transcript_id)
    )

    if around is not None:
        target_id = db.scalar(
            select(Message.id).where(
                Message.transcript_id == transcript_id, Message.record_uuid == around
            )
        )
        if target_id is None:
            raise LookupError(f"record {around} not found in transcript {transcript_id}")
        ordinal = db.scalar(
            select(func.count(Message.id)).where(
                Message.transcript_id == transcript_id, Message.id < target_id
            )
        )
        effective_offset = max(0, ordinal - limit // 2)
    else:
        effective_offset = max(offset, 0)

    messages = db.execute(
        select(Message)
        .where(Message.transcript_id == transcript_id)
        .order_by(Message.id)
        .offset(effective_offset)
        .limit(limit)
    ).scalars().all()

    items = [_message_out(db, m) for m in messages]
    return MessageList(items=items, total=total or 0, offset=effective_offset)


def _message_out(db: Session, message: Message) -> MessageOut:
    blocks = db.execute(
        select(ContentBlock)
        .where(ContentBlock.message_id == message.id)
        .order_by(ContentBlock.block_index)
    ).scalars().all()
    return MessageOut(
        record_uuid=message.record_uuid,
        parent_uuid=message.parent_uuid,
        type=message.type,
        model=message.model,
        timestamp=message.timestamp,
        blocks=[BlockOut.model_validate(b) for b in blocks],
    )
