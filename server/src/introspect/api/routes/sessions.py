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

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, case, exists, false, func, or_, select, true
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
from introspect.schema.authorship import CHAT_KINDS
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


class OriginCounts(BaseModel):
    """Session counts per `origin` value (Task T13), computed over the SAME `list_sessions`
    filters as the page EXCEPT the `origin=` filter itself -- see `list_sessions`."""

    root: int
    subagent: int
    empty: int


class SessionList(BaseModel):
    items: list[SessionSummary]
    total: int
    origin_counts: OriginCounts


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


#: The three §T13 authorship kinds that make a message "human-authored" for origin purposes
#: -- deliberately NARROWER than either CHAT_KINDS or DIALOGUE_KINDS in schema/authorship.py
#: (both of which also count Claude's own turns as "chat"): origin asks who, if anyone, typed
#: into this session, so only the human-stamped kinds count.
_HUMAN_AUTHORED_KINDS = frozenset({"human_typed", "human_queued", "human_inferred"})

#: The three `origin=` values the sessions list accepts (Task T13).
ORIGIN_SLUGS = ("root", "subagent", "empty")


def _session_has_human_message() -> ColumnElement:
    """EXISTS: the session has >=1 message with a human-authored ``authorship_kind`` in ANY
    of its transcripts (main or subagent) -- the ``root`` half of `_session_origin()` below.
    Same session-correlated EXISTS shape as `_not_archived()` above (not the scalar-subquery
    shape `_main_message_count`/`_is_favorited` use, since this needs no COUNT, just presence).
    """
    return (
        select(1)
        .select_from(Message)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(
            Transcript.session_id == ChatSession.session_uuid,
            Message.authorship_kind.in_(_HUMAN_AUTHORED_KINDS),
        )
        .correlate(ChatSession)
        .exists()
    )


def _session_has_any_message() -> ColumnElement:
    """EXISTS: the session has >=1 message at all, in any transcript -- the ``empty`` half of
    `_session_origin()` below."""
    return (
        select(1)
        .select_from(Message)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(Transcript.session_id == ChatSession.session_uuid)
        .correlate(ChatSession)
        .exists()
    )


def _session_origin() -> ColumnElement:
    """Session origin (Task T13, owner ruling 2026-09-24), correlated to `ChatSession`, same
    reuse shape as `_main_message_count`/`_is_favorited` above -- imported directly by
    `routes/search.py` so the two routes can never define the rule twice.

    ``root``: >=1 message anywhere in the session is human-authored (a human actually typed
    or queued something). ``empty``: the session has zero messages at all. ``subagent``: the
    floor -- messages exist, none human-authored, e.g. a standalone dispatched run's own
    session recording (a security review, a minion implementation) that no human ever typed
    into. Root is checked first so the (impossible in practice, since a human message implies
    >=1 message) overlap with "no messages" always resolves to root, not empty.
    """
    return case(
        (_session_has_human_message(), "root"),
        (~_session_has_any_message(), "empty"),
        else_="subagent",
    )


def _parse_origin_param(origin: str | None) -> frozenset[str] | None:
    """CSV of `origin=` slugs -> a validated set, or `None` when the param was not given at
    all (unfiltered). Task T13; error style matches `_parse_select_param` below (an empty
    value and any unrecognized slug are both 422s naming the valid set) -- deliberately UNLIKE
    `_parse_projects_param`, whose empty-list case means "no chips selected" == unfiltered;
    `origin=` has no chip-list UI precedent to honor, so an explicit-but-empty value is a
    caller bug, exactly like `select=`.
    """
    if origin is None:
        return None
    slugs = [s.strip() for s in origin.split(",") if s.strip()]
    valid = ", ".join(ORIGIN_SLUGS)
    if not slugs:
        raise HTTPException(status_code=422, detail=f"origin must not be empty -- valid: {valid}")
    unknown = [s for s in slugs if s not in ORIGIN_SLUGS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown origin {'slug' if len(unknown) == 1 else 'slugs'} "
            f"{', '.join(unknown)} -- valid: {valid}",
        )
    return frozenset(slugs)


def _not_archived() -> ColumnElement:
    """Predicate excluding archived sessions from any query selecting ``ChatSession`` (§15.1).

    A correlated ``NOT EXISTS`` over ``archived_sessions`` -- the single exclusion applied to the
    list AND the detail read paths so an archived session vanishes uniformly (list: absent;
    detail: ``row is None`` -> 404). It sits OUTSIDE ``list_sessions``' ``q=`` OR-predicate, so a
    session matched only by conversational content (§14.1) is still hidden. The messages/export
    read paths can't use this correlated form (they don't select ``ChatSession``) and instead
    probe ``archived_sessions`` directly -- see ``list_messages`` and the admin export route.
    A third named use site, ``list_projects``, folds it into the outer join's ON clause (not a
    WHERE) so a project whose sessions are all archived still lists at ``session_count`` 0
    instead of disappearing from the response entirely.
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
    origin: str,
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
        origin=origin,
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
            .outerjoin(
                ChatSession,
                and_(ChatSession.project_id == Project.id, _not_archived()),
            )
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
    origin: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> SessionList:
    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)
    project_slugs = _parse_projects_param(projects)
    origin_slugs = _parse_origin_param(origin)  # may raise 422; parsed before any query runs

    message_count = _main_message_count()
    favorited = _is_favorited()
    user_title = _user_title()
    origin_expr = _session_origin()

    stmt = (
        select(
            ChatSession, Project.dir_slug, message_count, favorited, user_title,
            origin_expr.label("origin"),
        )
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

    # `origin_counts` (Task T13): the SAME filters as `stmt` above EXCEPT `origin=` itself, so
    # the UI can say "N subagent sessions hidden" while showing e.g. root only. `stmt` at this
    # point already SELECTs `origin_expr` (labeled "origin") -- wrap it once and GROUP BY that
    # column rather than re-embedding the correlated EXISTS pair a second time.
    counts_subquery = stmt.subquery()
    origin_counts = {slug: 0 for slug in ORIGIN_SLUGS}
    for origin_value, count in db.execute(
        select(counts_subquery.c.origin, func.count()).group_by(counts_subquery.c.origin)
    ).all():
        origin_counts[origin_value] = count

    if origin_slugs is not None:
        stmt = stmt.where(origin_expr.in_(origin_slugs))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(
        ChatSession.last_activity_at.is_(None),  # non-NULL first -> NULLS LAST under DESC
        ChatSession.last_activity_at.desc(),
        ChatSession.session_uuid,  # deterministic tiebreaker for equal timestamps
    ).limit(limit).offset(offset)

    items = [
        _summary(session, slug, count, fav, u_title, origin_value)
        for (session, slug, count, fav, u_title, origin_value) in db.execute(stmt).all()
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
            best = snippets.get(item.session_uuid)
            if best is not None:
                # All three fields move together — the location pair is meaningful only where a
                # snippet is (same attribution rule; SessionSummary defaults them to None).
                item.match_snippet = best.snippet
                item.match_record_uuid = best.record_uuid
                item.match_agent_hex_id = best.agent_hex_id

    return SessionList(
        items=items, total=total or 0, origin_counts=OriginCounts(**origin_counts)
    )


@router.get("/sessions/{session_uuid}", response_model=SessionDetail)
def get_session(
    session_uuid: str, request: Request, db: Session = Depends(get_db)
) -> SessionDetail:
    row = db.execute(
        select(
            ChatSession, Project.dir_slug, _main_message_count(), _is_favorited(),
            _user_title(), _session_origin(),
        )
        .join(Project, ChatSession.project_id == Project.id)
        # `_not_archived()` folds "archived" into the same 404 as "unknown session" (§15.1) --
        # an archived session must be indistinguishable from a missing one on the read path.
        .where(ChatSession.session_uuid == session_uuid, _not_archived())
    ).one_or_none()
    if row is None:
        raise LookupError(f"session {session_uuid} not found")

    session, slug, count, fav, u_title, origin_value = row
    transcripts = db.execute(
        select(Transcript)
        .where(Transcript.session_id == session_uuid)
        .order_by(Transcript.kind, Transcript.id)  # 'main' before 'subagent'
    ).scalars().all()

    live_path = request.app.state.source_root / slug / f"{session_uuid}.jsonl"

    summary = _summary(session, slug, count, fav, u_title, origin_value)
    return SessionDetail(
        **summary.model_dump(),
        transcripts=[TranscriptInfo.model_validate(t) for t in transcripts],
        on_disk=live_path.exists(),
    )


#: The legacy §14.4 "conversation only" type set -- everything a human said or pasted stays
#: IN, only `system`-type rows (CLI-internal chatter) are hidden. Attachments are IN by
#: relativityboy's ruling ("pasted things are things a human said") -- do not narrow this to
#: `("user", "assistant")`, that was a stale draft. Now doubles as the NULL-tolerance fallback
#: rule in `_view_filter` (authorship spec §5) for rows not yet backfilled with an
#: `authorship_kind`.
_LEGACY_TYPES = ("user", "assistant", "attachment")

#: Kinds with dedicated renderers, used to spot UNKNOWN kinds by exclusion (an unknown kind
#: renders a visible UnknownChip client-side, so it counts as content -- forward-tolerance).
_KNOWN_BLOCK_KINDS = ("text", "thinking", "tool_use", "tool_result", "image")

#: The three `view=` values the messages endpoint accepts (authorship spec §5).
VIEW_VALUES = ("chat", "chat-harness", "all")


def _prose_visible() -> ColumnElement:
    """Spec §4 (2026-08-04 refinements): a row is visible only when at least one block renders
    content in conversation mode. thinking (the ◌ glyph), tool_use, tool_result and empty text
    don't count; images and unknown kinds do. Layered on top of the kind/type predicate by
    `_view_filter` for both the `chat` and `chat-harness` views."""
    return exists(
        select(1).where(
            ContentBlock.message_id == Message.id,
            or_(
                and_(
                    ContentBlock.block_kind == "text",
                    ContentBlock.text_content.is_not(None),
                    ContentBlock.text_content != "",
                ),
                ContentBlock.block_kind == "image",
                ContentBlock.block_kind.not_in(_KNOWN_BLOCK_KINDS),
            ),
        )
    )


def _is_resolved_dispatch_block() -> ColumnElement:
    """True when the CORRELATED ``ContentBlock`` (whichever query embeds this predicate) is a
    ``tool_use`` that dispatched a captured subagent transcript -- the join a resolved
    :class:`SubagentChip` renders client-side (``Transcript.parent_tool_use_id ==
    ContentBlock.tool_use_id``).

    Factored out of `_has_resolved_dispatch()` (Task T12) so BOTH the message-level EXISTS below
    (`view=chat`/`chat-harness` row admission, final review C1) and
    `_block_matches_categories()`'s block-level claude-chat routing (`select=`, owner ruling
    2026-09-23: a resolved-dispatch `tool_use` block is claude-chat, not tool-traffic) share the
    IDENTICAL join -- one mechanism, two use sites, so they can never disagree on what counts as
    "resolved". ``.correlate(ContentBlock)`` is explicit (not relied on via auto-correlation)
    because this predicate is embedded several EXISTS-levels deep at its `_block_matches_categories`
    use site -- ``Transcript`` stays local to this inner EXISTS via ``select_from``, only
    ``ContentBlock`` correlates outward. SQL equality with NULL is never true, so a block with no
    ``tool_use_id``, or one no captured transcript claims, contributes nothing here -- no
    explicit IS NOT NULL guard needed."""
    return and_(
        ContentBlock.block_kind == "tool_use",
        exists(
            select(1)
            .select_from(Transcript)
            .where(Transcript.parent_tool_use_id == ContentBlock.tool_use_id)
        ).correlate(ContentBlock),
    )


def _has_resolved_dispatch() -> ColumnElement:
    """A row whose block set includes a ``tool_use`` that dispatched a captured subagent
    transcript -- the join a resolved :class:`SubagentChip` renders client-side.

    Layered onto ``_prose_visible()`` by `_view_filter` for `chat`/`chat-harness` (final review
    C1): an assistant transcript record carries ONE content block each, so a dispatch row's
    block IS its tool_use -- no text alongside it. ``_prose_visible()`` alone hides such a row
    (a dispatch tool_use never counts as "content"), which strands the chip -- the reader's sole
    doorway into that subagent transcript -- outside `all` even though spec §6/§10.7(c) mandate
    it visible in every view. Production: 445 dispatch rows, 0 with any prose."""
    return exists(
        select(1).where(
            ContentBlock.message_id == Message.id,
            _is_resolved_dispatch_block(),
        )
    )


def _view_filter(view: str) -> ColumnElement:
    """Spec §5. NULL-tolerant: rows not yet backfilled (migrate→reparse window) degrade
    to the legacy type+content rule, never to an empty reader."""
    if view == "all":
        return true()
    legacy_fallback = and_(
        Message.authorship_kind.is_(None), Message.type.in_(_LEGACY_TYPES)
    )
    if view == "chat":
        kind_ok = or_(Message.authorship_kind.in_(sorted(CHAT_KINDS)), legacy_fallback)
    else:  # chat-harness
        # NB: no `or_(_, legacy_fallback)` wrapper here, unlike `chat` above -- legacy_fallback
        # (kind IS NULL AND type qualifies) is already SUBSUMED by this branch's own first
        # disjunct (kind IS NULL), so OR-ing it in would be a no-op that only reads as if it did
        # something (final review minor, task 4 ledger). `chat`'s CHAT_KINDS membership test has
        # no such NULL disjunct, so its legacy_fallback term is load-bearing there.
        kind_ok = and_(
            or_(Message.authorship_kind.is_(None), Message.authorship_kind != "tool_result"),
            Message.type.in_(_LEGACY_TYPES),
        )
    return and_(kind_ok, or_(_prose_visible(), _has_resolved_dispatch()))


# --- select= category machinery (Task T9) ------------------------------------------------
#
# A finer-grained, block-level partition layered ALONGSIDE (not instead of) `view=`/
# `_view_filter` above: every (message, block) pair maps to EXACTLY ONE of five categories,
# derived from the SAME authorship-kind vocabulary `_view_filter` already reads (never a
# parallel rule set -- see `_categorize`'s docstring for the priority order). `_categorize` is
# the single Python source of truth, reused by three call sites: the SQL-side row predicate
# (`_select_filter`, via `_block_matches_categories`) for `list_messages`, the per-block prune
# in `_message_out`, and `routes/search.py`'s session-scope hit filter -- one function, three
# sites, so the SQL and Python paths can never drift (the `_QMatcher` pattern above already
# establishes this precedent in this same module). Task T12 grew its signature with a
# resolved-dispatch context (a `tool_use_id` + the ids that resolved to a captured subagent
# transcript) without changing this three-site reuse shape.
#
# Unlike `view=`, which only ever decides ROW visibility (a visible row's `blocks` array is
# returned whole, untouched, by `_message_out` today), `select=` filters at BOTH granularities:
# a row disappears iff none of its blocks are selected, AND a visible row's `blocks` array is
# pruned to only the selected blocks. This means `select=<preset-equivalent-set>` is NOT
# always byte-identical to the corresponding `view=` for a row whose blocks span more than one
# category (e.g. a claude turn with both `thinking` and `text`, or a tool call combined with
# narration text) -- see the write-up (claude_notes/2026-09-22-sdd-checkboxes-writeups.md) for
# the exact preset<->select-set equivalences this was proven against and the discovered nuances.

CATEGORY_SLUGS = ("you-chat", "claude-chat", "claude-thinking", "tool-traffic", "harness-system")

#: The human family: spec's four listed kinds (human_typed / human_queued / human_inferred /
#: attachment_queued_human) PLUS `interrupt_marker` -- not in the task brief's illustrative
#: list, but `CHAT_KINDS`/`DIALOGUE_KINDS` in schema/authorship.py both group it with the human
#: kinds ("they are the human's voice", authorship.py:29) and `view=chat`'s CHAT_KINDS
#: membership test treats it identically to the four listed kinds. Derived from the EXISTING
#: view logic (as instructed), not invented in parallel -- omitting it would desync
#: `select=you-chat,...` from `view=chat` for every interrupted turn.
_YOU_CHAT_AUTHORSHIP_KINDS = frozenset({
    "human_typed", "human_queued", "human_inferred", "attachment_queued_human", "interrupt_marker",
})

#: The claude family (spec: claude / dispatch / coordinator).
_CLAUDE_FAMILY_AUTHORSHIP_KINDS = frozenset({"claude", "dispatch", "coordinator"})


def _categorize(
    authorship_kind: str | None,
    block_kind: str,
    tool_use_id: str | None = None,
    resolved_dispatch_tool_use_ids: frozenset[str] = frozenset(),
) -> str:
    """Map one (message authorship kind, block kind) pair to its `select=` category slug
    (Task T9; resolved-dispatch routing added Task T12). Total over every input -- an
    unrecognized/NULL `authorship_kind` and any `block_kind` land on `harness-system`, the
    exhaustive floor, so a classification gap can never silently drop a block (a final
    else-branch category, never an omission).

    Priority, first match wins:
      1. every block of a ``tool_result``-authorship MESSAGE is ``tool-traffic`` (spec: "AND
         everything of tool_result-kind messages" -- the exchange is a unit).
      2. a ``tool_use`` BLOCK whose ``tool_use_id`` is in `resolved_dispatch_tool_use_ids` (a
         captured subagent transcript's ``parent_tool_use_id`` -- the SAME set
         `_resolved_dispatch_tool_use_ids()` below and `_is_resolved_dispatch_block()`'s SQL
         join both derive from) is ``claude-chat`` -- owner ruling 2026-09-23: a resolved
         dispatch is a doorway into a Claude-voiced conversation, not mechanical traffic. Any
         OTHER ``tool_use`` BLOCK (unresolved, or the resolved set omitted/empty) is
         ``tool-traffic`` regardless of its message's authorship (spec: "wherever they appear")
         -- checked before the family branches below so a tool_use block on a
         claude/dispatch/coordinator message routes here, not to claude-chat/claude-thinking,
         unless the resolved case above already claimed it.
      3. the human family (`_YOU_CHAT_AUTHORSHIP_KINDS`) -> ``you-chat``.
      4. the claude family (`_CLAUDE_FAMILY_AUTHORSHIP_KINDS`): a ``thinking`` block ->
         ``claude-thinking``, everything else -> ``claude-chat``.
      5. everything else (system records, skill/command furniture, notifications, non-rescued
         attachments, unclassified/NULL authorship, a `thinking` block outside the claude
         family, ...) -> ``harness-system``.
    """
    if authorship_kind == "tool_result":
        return "tool-traffic"
    if block_kind == "tool_use":
        if tool_use_id is not None and tool_use_id in resolved_dispatch_tool_use_ids:
            return "claude-chat"
        return "tool-traffic"
    if authorship_kind in _YOU_CHAT_AUTHORSHIP_KINDS:
        return "you-chat"
    if authorship_kind in _CLAUDE_FAMILY_AUTHORSHIP_KINDS:
        return "claude-thinking" if block_kind == "thinking" else "claude-chat"
    return "harness-system"


def _resolved_dispatch_tool_use_ids(db: Session) -> frozenset[str]:
    """The set of every ``tool_use_id`` some CAPTURED subagent transcript claims as its
    ``parent_tool_use_id`` -- the id-set projection of `_is_resolved_dispatch_block()`'s join,
    used by `_message_out`'s per-block `select=` prune (Task T12), which classifies in Python
    via `_categorize` rather than SQL and so needs the resolved set materialized once per
    request rather than re-querying per block. Deliberately unscoped by session/transcript,
    matching `_is_resolved_dispatch_block()`'s own unscoped join -- ``tool_use_id``s are
    globally unique, so scoping would only add complexity, not correctness."""
    return frozenset(
        db.scalars(
            select(Transcript.parent_tool_use_id).where(Transcript.parent_tool_use_id.is_not(None))
        )
    )


def _block_matches_categories(categories: frozenset[str]) -> ColumnElement:
    """SQL-side twin of `_categorize`, built as an explicit OR of the SAME priority branches
    (kept in lockstep by the equivalence tests) rather than calling `_categorize` per row --
    this predicate runs inside the EXISTS subquery `_select_filter` builds below, correlated
    to a `ContentBlock` joined to its owning `Message` in that same subquery.

    One refinement beyond a bare port of `_categorize`: an EMPTY ``text`` block never matches
    any category here, even though `_categorize` (a pure classifier, no notion of "empty")
    would still slot it into its message's family. This mirrors `_prose_visible()`'s own
    ``text_content IS NOT NULL AND text_content <> ''`` condition above and is what the task
    brief's "same disappear rule views use" phrase calls for: without it, a tool-call row's
    routinely-empty companion text block (the CLI always emits one alongside a `tool_use`, per
    `_has_resolved_dispatch()`'s own count: 445 production rows, 0 with any prose) would, on
    its own, make the row "have a selected block" under e.g. `select=claude-chat` even though
    there is nothing to show -- a content-free row `view=chat`/`chat-harness` would never
    surface either (`_prose_visible()` fails there for the identical reason). This check is
    scoped to ROW VISIBILITY only (this EXISTS) -- the separate per-block prune in
    `_message_out` (driven by `_categorize`, given the SAME resolved-dispatch set) still returns
    an empty text block verbatim once its row is visible via some OTHER real block, exactly like
    `view=` does today for an already-visible row.

    A SECOND refinement (Task T12, owner ruling 2026-09-23): a ``tool_use`` block that resolved
    to a CAPTURED subagent transcript is ``claude-chat``, not ``tool-traffic`` -- reusing
    `_is_resolved_dispatch_block()`'s exact join (the same one `_has_resolved_dispatch()` above
    uses for `view=`) rather than inventing a parallel resolution rule.
    """
    is_nonempty_or_not_text = or_(
        ContentBlock.block_kind != "text",
        and_(ContentBlock.text_content.is_not(None), ContentBlock.text_content != ""),
    )
    # NOTE(claude): every `Message.authorship_kind`-based check below is guarded with an
    # explicit `is_not(None)` -- SQL's three-valued logic makes `NULL == 'x'` and
    # `NULL IN (...)` evaluate to NULL (neither true nor false), which would otherwise poison
    # the `~`/`and_` chains for a not-yet-classified row (NULL authorship_kind: the
    # migrate->reparse window `_view_filter`'s own `legacy_fallback` above is NULL-tolerant
    # about) into matching NO branch at all, including harness-system -- the opposite of
    # `_categorize(None, ...)`'s Python behavior, which correctly floors an unrecognized/None
    # kind to harness-system via a plain `in` test. Guarding makes each check a real
    # true/false, not NULL, so `~is_you_chat_message`/`~is_claude_family_message` come out
    # `True` for a NULL row and the harness-system branch (which needs exactly that) fires.
    authorship_known = Message.authorship_kind.is_not(None)
    is_tool_result_message = and_(authorship_known, Message.authorship_kind == "tool_result")
    is_tool_use_block = ContentBlock.block_kind == "tool_use"
    is_resolved_dispatch_block = _is_resolved_dispatch_block()
    is_you_chat_message = and_(
        authorship_known, Message.authorship_kind.in_(_YOU_CHAT_AUTHORSHIP_KINDS)
    )
    is_claude_family_message = and_(
        authorship_known, Message.authorship_kind.in_(_CLAUDE_FAMILY_AUTHORSHIP_KINDS)
    )
    is_thinking_block = ContentBlock.block_kind == "thinking"

    branches: list[ColumnElement] = []
    if "tool-traffic" in categories:
        branches.append(
            or_(is_tool_result_message, and_(is_tool_use_block, ~is_resolved_dispatch_block))
        )
    if "you-chat" in categories:
        branches.append(and_(~is_tool_result_message, ~is_tool_use_block, is_you_chat_message))
    if "claude-thinking" in categories:
        branches.append(
            and_(
                ~is_tool_result_message, ~is_tool_use_block,
                is_claude_family_message, is_thinking_block,
            )
        )
    if "claude-chat" in categories:
        branches.append(
            or_(
                and_(
                    ~is_tool_result_message, ~is_tool_use_block,
                    is_claude_family_message, ~is_thinking_block,
                ),
                and_(~is_tool_result_message, is_resolved_dispatch_block),
            )
        )
    if "harness-system" in categories:
        branches.append(
            and_(
                ~is_tool_result_message, ~is_tool_use_block,
                ~is_you_chat_message, ~is_claude_family_message,
            )
        )
    return and_(is_nonempty_or_not_text, or_(*branches) if branches else false())


def _select_filter(categories: frozenset[str]) -> ColumnElement:
    """A message row is visible iff it has >=1 block whose category is in `categories` (Task
    T9's disappear rule) -- an EXISTS predicate, so it plugs into the same four query sites
    `list_messages` already threads `type_filter` through (total / anchor resolution / ordinal
    count / page fetch), exactly like `_view_filter` above."""
    return exists(
        select(1).where(
            ContentBlock.message_id == Message.id,
            _block_matches_categories(categories),
        )
    )


def _parse_select_param(select_param: str | None) -> frozenset[str] | None:
    """CSV of category slugs -> a validated set, or `None` when `select=` was not given at all
    (the caller falls back to `view=`). Raises `HTTPException(422)` for an empty selection
    (`select=` -- "a selection of nothing is a caller bug, not 'show nothing'", spec) or any
    unrecognized slug (named, alongside the valid set) -- caught by the app's registered
    `StarletteHTTPException` handler (errors.py) into the same problem-JSON shape every other
    422 in this API uses, from whichever route calls this (also imported by `routes/search.py`).
    """
    if select_param is None:
        return None
    slugs = [s.strip() for s in select_param.split(",") if s.strip()]
    valid = ", ".join(CATEGORY_SLUGS)
    if not slugs:
        raise HTTPException(
            status_code=422, detail=f"select must not be empty -- valid categories: {valid}"
        )
    unknown = [s for s in slugs if s not in CATEGORY_SLUGS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown select {'category' if len(unknown) == 1 else 'categories'} "
            f"{', '.join(unknown)} -- valid: {valid}",
        )
    return frozenset(slugs)


@router.get("/transcripts/{transcript_id}/messages", response_model=MessageList)
def list_messages(
    transcript_id: int,
    db: Session = Depends(get_db),
    offset: int = 0,
    limit: int = _DEFAULT_LIMIT,
    around: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    until: str | None = None,
    view: Literal["chat", "chat-harness", "all"] = "all",
    # NOTE(claude): NOT named `select` -- `sqlalchemy.select` is imported into this module's
    # namespace and used throughout this very function; a param named `select` would shadow it.
    # Aliased exactly like `from_`/`from` above.
    select_: str | None = Query(default=None, alias="select"),
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

    # Task T9: `select=` takes precedence over `view=` when both are given -- parsed first (may
    # raise a 422) so an invalid `select=` fails fast, before any query runs.
    categories = _parse_select_param(select_)

    # Built ONCE, applied at all four query sites below (total, around-target resolution,
    # around ordinal count, page fetch) -- missing any one desyncs totals/offsets/centering
    # (see module docstring + task-p4-5-brief.md). `view="all"` yields `True` (no-op filter),
    # so the default path's generated SQL/results are unchanged. `_view_filter()`'s two
    # EXISTS-over-blocks clauses (`_prose_visible()` and `_has_resolved_dispatch()`) ride along
    # automatically since the filter is still built once here and reused at all four sites
    # (authorship spec §5; resolved-dispatch rows, final review C1). `_select_filter()` is the
    # T9 sibling: same four-site reuse, sourced from `categories` instead of `view` whenever a
    # `select=` was given.
    type_filter: ColumnElement = (
        _select_filter(categories) if categories is not None else _view_filter(view)
    )

    total = db.scalar(
        select(func.count(Message.id)).where(
            Message.transcript_id == transcript_id, type_filter
        )
    )

    # Three anchor modes share one resolution; they differ only in where the window sits
    # relative to the anchor: `around` centers, `from` starts AT it, `until` ends AT it.
    anchors = {
        name: value
        for name, value in (("around", around), ("from", from_), ("until", until))
        if value is not None
    }
    if len(anchors) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "around, from and until are mutually exclusive; got "
                + ", ".join(sorted(anchors))
            ),
        )

    effective_limit = limit
    if anchors:
        ((anchor_name, anchor_uuid),) = anchors.items()
        target_id = db.scalar(
            select(Message.id).where(
                Message.transcript_id == transcript_id,
                Message.record_uuid == anchor_uuid,
                type_filter,
            )
        )
        if target_id is None:
            raise LookupError(
                f"record {anchor_uuid} not found in transcript {transcript_id}"
            )
        ordinal = db.scalar(
            select(func.count(Message.id)).where(
                Message.transcript_id == transcript_id,
                Message.id < target_id,
                type_filter,
            )
        )
        if anchor_name == "around":
            effective_offset = max(0, ordinal - limit // 2)
        elif anchor_name == "from":
            effective_offset = ordinal
        else:  # until: clamp AND truncate -- a row past the anchor is a broken promise,
            # so the early-anchor clamp shrinks the page instead of sliding it (unlike
            # around, whose centered clamp deliberately keeps a full window).
            effective_offset = max(0, ordinal - limit + 1)
            effective_limit = ordinal - effective_offset + 1
    else:
        effective_offset = max(offset, 0)

    messages = db.execute(
        select(Message)
        .where(Message.transcript_id == transcript_id, type_filter)
        .order_by(Message.id)
        .offset(effective_offset)
        .limit(effective_limit)
    ).scalars().all()

    # Task T12: only fetched when `select=` was given -- `_categorize`'s resolved-dispatch arm is
    # a no-op without it (an empty set never matches any `tool_use_id`), and `view=` mode never
    # calls `_categorize` at all (its blocks return whole, untouched, below).
    resolved_dispatch_tool_use_ids = (
        _resolved_dispatch_tool_use_ids(db) if categories is not None else frozenset()
    )
    items = [_message_out(db, m, categories, resolved_dispatch_tool_use_ids) for m in messages]
    return MessageList(items=items, total=total or 0, offset=effective_offset)


def _message_out(
    db: Session,
    message: Message,
    categories: frozenset[str] | None = None,
    resolved_dispatch_tool_use_ids: frozenset[str] = frozenset(),
) -> MessageOut:
    blocks = db.execute(
        select(ContentBlock)
        .where(ContentBlock.message_id == message.id)
        .order_by(ContentBlock.block_index)
    ).scalars().all()
    if categories is not None:
        # Task T9: unlike `view=` (which never filters blocks within an already-visible row),
        # `select=` prunes to only the SELECTED blocks -- "a block renders iff its category is
        # selected". The row itself was already admitted by `_select_filter`'s EXISTS above
        # (>=1 block selected), so this can never prune a row down to zero blocks. Task T12:
        # `resolved_dispatch_tool_use_ids` routes a resolved dispatch's `tool_use` block to
        # claude-chat here too, so a chip pruned-in by `_select_filter` doesn't get pruned back
        # OUT by this second, independent classification pass.
        blocks = [
            b
            for b in blocks
            if _categorize(
                message.authorship_kind, b.block_kind, b.tool_use_id,
                resolved_dispatch_tool_use_ids,
            )
            in categories
        ]
    return MessageOut(
        record_uuid=message.record_uuid,
        parent_uuid=message.parent_uuid,
        type=message.type,
        model=message.model,
        timestamp=message.timestamp,
        blocks=[BlockOut.model_validate(b) for b in blocks],
        authorship_kind=message.authorship_kind,
        authorship_basis=message.authorship_basis,
        authorship_detail=message.authorship_detail,
    )
