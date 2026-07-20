"""Read endpoints for projects, sessions, and transcript messages (Task P2-5).

Every handler is read-only: it opens a request-scoped :class:`~sqlalchemy.orm.Session` via
:func:`introspect.api.deps.get_db` and constructs the pinned response models from
:mod:`introspect.api.models` (favorites are written by Task 7, user titles by Task P4-1,
never here). Two shape rules are load-bearing and enforced by the spec:

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
from sqlalchemy import ColumnElement, func, or_, select, true
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import (
    _DEFAULT_LIMIT,
    _MAX_LIMIT,
    BlockOut,
    MessageOut,
    SessionDetail,
    SessionSummary,
    TranscriptInfo,
)
from introspect.models import (
    ArchivedSession,
    ChatSession,
    ContentBlock,
    Favorite,
    Message,
    Project,
    Transcript,
    UserTitle,
)
from introspect.search import get_search_index

router = APIRouter(prefix="/api/v1")


class _QMatcher:
    """The single source of truth for the ``q=`` substring match, shared by the SQL predicate
    and the per-row Python re-check so the two can never drift (critique F9).

    The one semantic both sides implement: *does ``lower(text)`` contain ``lower(q)`` as a
    LITERAL substring?* The SQL side renders it as ``lower(col) LIKE '%'||escaped||'%' ESCAPE
    '\\'`` where every ``%``/``_``/``\\`` in ``q`` is escaped to a literal (the P2 ledger flags
    that the old ``title=`` path left these wild -- we do not carry that forward). The Python
    side is a plain ``in`` over the *same* ``_q_lower``. Both derive from that one field, so the
    only place the needle is built is here.
    """

    _ESCAPE = "\\"

    def __init__(self, q: str) -> None:
        self._q_lower = q.lower()

    def _like_needle(self) -> str:
        # Escape the escape char FIRST, then the two LIKE wildcards -> every metacharacter in q
        # becomes a literal under ``ESCAPE '\\'``.
        escaped = (
            self._q_lower.replace(self._ESCAPE, self._ESCAPE * 2)
            .replace("%", self._ESCAPE + "%")
            .replace("_", self._ESCAPE + "_")
        )
        return f"%{escaped}%"

    def sql_predicate(self, *columns: ColumnElement) -> ColumnElement:
        """OR of ``lower(col) LIKE <literal needle>`` across the given columns/subqueries."""
        needle = self._like_needle()
        return or_(*(func.lower(col).like(needle, escape=self._ESCAPE) for col in columns))

    def matches_text(self, *values: str | None) -> bool:
        """True if any value contains ``q`` as a literal substring -- the Python twin of
        :meth:`sql_predicate`, used for per-page match attribution.

        NOTE(claude): identical to the SQL side across the ASCII domain (every fixture + every
        real title/uuid we ingest). The one theoretical divergence is case-folding of non-ASCII
        letters: SQLite's built-in ``lower()`` folds ASCII only, while Python's ``str.lower()``
        folds Unicode, so a non-ASCII-cased title could be attributed differently here than the
        SQL predicate selected it. Fixing that means ICU-aware folding on both sides (out of
        scope); flag it if titles ever carry cased non-ASCII text.
        """
        return any(v is not None and self._q_lower in v.lower() for v in values)


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


def _user_title():
    """The user-set title text for the session, or NULL if none set; correlated to ``ChatSession``."""
    return (
        select(UserTitle.title)
        .where(UserTitle.session_uuid == ChatSession.session_uuid)
        .correlate(ChatSession)
        .scalar_subquery()
    )


def _not_archived() -> ColumnElement:
    """Predicate excluding archived sessions from any query selecting ``ChatSession`` (§15.1).

    A correlated ``NOT EXISTS`` over ``archived_sessions`` -- the single exclusion applied to the
    list AND the detail read paths so an archived session vanishes uniformly (list: absent;
    detail: ``row is None`` -> 404). It sits OUTSIDE ``list_sessions``' ``q=`` OR-predicate, so a
    session matched only by conversational content (§14.1) is still hidden. The messages/export
    read paths can't use this correlated form (they don't select ``ChatSession``) and instead
    probe ``archived_sessions`` directly -- see ``list_messages`` and the admin export route.
    """
    return ~(
        select(ArchivedSession.session_uuid)
        .where(ArchivedSession.session_uuid == ChatSession.session_uuid)
        .correlate(ChatSession)
        .exists()
    )


def _parse_projects_param(projects: str | None) -> list[str] | None:
    """Parse the ``projects=`` comma-list query param into a ``SearchIndex``-shaped filter.

    Shared by ``list_sessions`` here and ``search`` in ``routes/search.py`` so the two routes
    can never drift on comma-parsing. Split on commas, stripping whitespace and empties;
    absent (``None``) and present-but-empty (``?projects=`` or an all-empty comma list, e.g.
    ``?projects=,,``) both map to ``None`` -- an empty chip list from the UI means "no chips
    selected", i.e. unfiltered, which is the ``SearchIndex`` Protocol's ``None`` (its ``[]``
    is reserved for an explicit-but-non-matching filter, never produced by this parser).
    Unknown slugs are passed through as-is with no validation -- they simply match nothing
    downstream (client renders raw-slug chips; server stays dumb).
    """
    if not projects:
        return None
    slugs = [slug.strip() for slug in projects.split(",") if slug.strip()]
    return slugs or None


def _summary(
    session: ChatSession,
    project_slug: str,
    message_count: int,
    favorite: int,
    user_title: str | None,
) -> SessionSummary:
    return SessionSummary(
        session_uuid=session.session_uuid,
        project_slug=project_slug,
        ai_title=session.ai_title,
        custom_title=session.custom_title,
        user_title=user_title,
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
    q: str | None = None,
    favorite: bool | None = None,
    projects: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> SessionList:
    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)
    project_slugs = _parse_projects_param(projects)

    message_count = _main_message_count()
    favorited = _is_favorited()
    user_title = _user_title()

    stmt = (
        select(ChatSession, Project.dir_slug, message_count, favorited, user_title)
        .join(Project, ChatSession.project_id == Project.id)
        # Archived sessions are hidden from the list (§15.1). Applied to the base statement so it
        # flows into both `total` (via stmt.subquery()) and the page, and stays OUTSIDE the `q=`
        # OR-predicate below so a content-only match to an archived session is still excluded.
        .where(_not_archived())
    )

    # `q=` unites three match kinds in ONE OR-predicate (no re-rank, so the three-key ordering
    # below carries through unchanged): (a) case-insensitive uuid substring, (b) LIKE over the
    # archive titles + the user title (OR across columns, never COALESCE -- a user rename must
    # not shadow an archive-title hit, critique #5), (c) FTS content membership. The content
    # set comes from the search index (no raw FTS SQL in this route). `project_slugs` is
    # threaded into the content pass too (Task 4): it narrows session_uuids_matching's
    # corpus-wide FTS scan server-side, matching the outer `Project.dir_slug IN (...)` filter
    # below so the two never disagree. `content_uuids` is consumed as a SET.
    matcher: _QMatcher | None = None
    content_uuids: set[str] = set()
    if q:
        matcher = _QMatcher(q)
        content_uuids = set(
            get_search_index().session_uuids_matching(db, q, project_slugs=project_slugs)
        )
        predicate = matcher.sql_predicate(
            ChatSession.session_uuid,
            ChatSession.ai_title,
            ChatSession.custom_title,
            user_title,
        )
        if content_uuids:
            predicate = or_(predicate, ChatSession.session_uuid.in_(content_uuids))
        stmt = stmt.where(predicate)
    if favorite:
        stmt = stmt.where(favorited > 0)
    if project_slugs is not None:
        stmt = stmt.where(Project.dir_slug.in_(project_slugs))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(
        ChatSession.last_activity_at.is_(None),  # non-NULL first -> NULLS LAST under DESC
        ChatSession.last_activity_at.desc(),
        ChatSession.session_uuid,  # deterministic tiebreaker for equal timestamps
    ).limit(limit).offset(offset)

    items = [
        _summary(session, slug, count, fav, u_title)
        for (session, slug, count, fav, u_title) in db.execute(stmt).all()
    ]

    if matcher is not None:
        # The OR-predicate doesn't reveal WHICH disjunct matched, so re-check each page row in
        # Python (byte-identical matcher) to find the content-ONLY matches -- those, and only
        # those, get a snippet. ONE batched best_snippets() call covers the whole page.
        content_only = [
            item.session_uuid
            for item in items
            if item.session_uuid in content_uuids
            and not matcher.matches_text(
                item.session_uuid, item.ai_title, item.custom_title, item.user_title
            )
        ]
        snippets = (
            get_search_index().best_snippets(db, content_only, q) if content_only else {}
        )
        for item in items:
            item.match_snippet = snippets.get(item.session_uuid)

    return SessionList(items=items, total=total or 0)


@router.get("/sessions/{session_uuid}", response_model=SessionDetail)
def get_session(session_uuid: str, db: Session = Depends(get_db)) -> SessionDetail:
    row = db.execute(
        select(ChatSession, Project.dir_slug, _main_message_count(), _is_favorited(), _user_title())
        .join(Project, ChatSession.project_id == Project.id)
        # `_not_archived()` folds "archived" into the same 404 as "unknown session" (§15.1) --
        # an archived session must be indistinguishable from a missing one on the read path.
        .where(ChatSession.session_uuid == session_uuid, _not_archived())
    ).one_or_none()
    if row is None:
        raise LookupError(f"session {session_uuid} not found")

    session, slug, count, fav, u_title = row
    transcripts = db.execute(
        select(Transcript)
        .where(Transcript.session_id == session_uuid)
        .order_by(Transcript.kind, Transcript.id)  # 'main' before 'subagent'
    ).scalars().all()

    summary = _summary(session, slug, count, fav, u_title)
    return SessionDetail(
        **summary.model_dump(),
        transcripts=[TranscriptInfo.model_validate(t) for t in transcripts],
    )


#: The §14.4 "conversation only" predicate: everything a human said or pasted stays IN,
#: only `system`-type rows (CLI-internal chatter) are hidden. Attachments are IN by
#: Donovan's ruling ("pasted things are things a human said") -- do not narrow this to
#: `("user", "assistant")`, that was a stale draft.
_CHAT_ONLY_TYPES = ("user", "assistant", "attachment")


@router.get("/transcripts/{transcript_id}/messages", response_model=MessageList)
def list_messages(
    transcript_id: int,
    db: Session = Depends(get_db),
    offset: int = 0,
    limit: int = _DEFAULT_LIMIT,
    around: str | None = None,
    chat_only: bool = False,
) -> MessageList:
    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise LookupError(f"transcript {transcript_id} not found")
    # An archived session hides ALL its transcripts (main + subagents), so a request for one is
    # the same 404 as an unknown transcript (§15.1). `session_id` on a subagent transcript is the
    # PARENT session's uuid, so this one check covers the drill-in reader too.
    if db.get(ArchivedSession, transcript.session_id) is not None:
        raise LookupError(f"transcript {transcript_id} not found")

    limit = min(max(limit, 1), _MAX_LIMIT)

    # Built ONCE, applied at all four query sites below (total, around-target resolution,
    # around ordinal count, page fetch) -- missing any one desyncs totals/offsets/centering
    # (see module docstring + task-p4-5-brief.md). `True` (no-op filter) when chat_only is
    # off, so the default path's generated SQL/results are unchanged.
    type_filter: ColumnElement = (
        Message.type.in_(_CHAT_ONLY_TYPES) if chat_only else true()
    )

    total = db.scalar(
        select(func.count(Message.id)).where(
            Message.transcript_id == transcript_id, type_filter
        )
    )

    if around is not None:
        target_id = db.scalar(
            select(Message.id).where(
                Message.transcript_id == transcript_id,
                Message.record_uuid == around,
                type_filter,
            )
        )
        if target_id is None:
            raise LookupError(f"record {around} not found in transcript {transcript_id}")
        ordinal = db.scalar(
            select(func.count(Message.id)).where(
                Message.transcript_id == transcript_id,
                Message.id < target_id,
                type_filter,
            )
        )
        effective_offset = max(0, ordinal - limit // 2)
    else:
        effective_offset = max(offset, 0)

    messages = db.execute(
        select(Message)
        .where(Message.transcript_id == transcript_id, type_filter)
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
