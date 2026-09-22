"""Full-text search endpoint (Task P2-6): ``GET /api/v1/search``.

Two response shapes share one query path through
:func:`introspect.search.get_search_index`:

* ``scope=session`` (requires ``session=<uuid>``) returns a flat, rank-ordered page of hits
  -- ``limit``/``offset`` apply directly to the underlying FTS5 query.
* ``scope=global`` (default) returns hits GROUPED by session. The grouping is a client-side
  VIEW over one page of hits, not a separate per-session query: ``limit``/``offset`` are
  applied to the flat, rank-ordered hit list FIRST (one ``search()`` call), and the returned
  page is then partitioned by session. **Pagination is therefore over HITS, not over groups or
  sessions** -- a session's hits can be split across two ``offset=`` pages, and a group's
  ``has_more`` reflects only that group's hit count WITHIN THE RETURNED PAGE (a session could
  have further matches beyond the page that ``has_more`` does not represent). Groups are
  ordered by their best (lowest bm25) rank, which falls out for free from the hit list already
  being rank-ordered: a session's first appearance in that list is necessarily its best-ranked
  hit (see :func:`_group_hits`). Each group's hits are capped at :data:`_GROUP_CAP`;
  ``has_more`` is set when a group carries more than the cap within the page.

``total`` in both shapes is the total matching hit count for the query (and session filter,
for ``scope=session``) as returned by :meth:`SearchIndex.search` -- page-independent, pre-cap,
and pre-grouping; identical in meaning to the ``total`` the sessions/messages endpoints report.

``projects=`` (Task 4; parsed by :func:`introspect.api.routes.sessions._parse_projects_param`,
shared with ``routes/sessions.py`` so the two routes can never drift on comma-parsing) narrows
``scope=global`` to the given ``dir_slug``s. ``scope=session`` accepts-and-IGNORES it (critique
#7): threading a project filter into a search that is already pinned to one session risks
filtering out the very session being read.

Empty/whitespace ``q`` and a missing ``session`` under ``scope=session`` are the only two
request-shape errors this route rejects, both as inline 422 problem responses (see
:func:`_problem`) rather than through ``RequestValidationError`` -- simpler than constructing
a synthetic validation-error payload, and the resulting JSON is the same ``{status, title,
detail}`` shape :mod:`introspect.api.errors` produces. No other input can fail:
:func:`introspect.search.sanitize_query` guarantees every ``q`` sanitizes to a syntactically
valid (possibly empty-match) FTS5 query, so nothing here wraps ``search()`` in a try/except --
doing so would risk masking a real corruption error as an empty result, which the sanitizer's
never-raise guarantee is specifically meant to make unnecessary.

**Id-shaped ``q`` bypasses FTS entirely (Task T3).** A ``q`` matching
:data:`_ID_SHAPED_QUERY_RE` (a bare ``msg_...`` api-message-id, never a phrase) skips
``SearchIndex.search`` and instead does an exact-match lookup on ``Message.api_message_id``
via :func:`_api_message_id_hits`. The lookup returns the same ``(list[SearchHit], int)``
shape ``SearchIndex.search`` does, so every downstream step -- archived-drop, subagent-hex
resolution, ``HitOut`` construction, global-scope grouping -- runs unchanged over hits from
either path; the two response shapes never had to special-case which path produced them.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import _DEFAULT_LIMIT, _MAX_LIMIT, HitOut, Problem, SessionSummary
from introspect.api.routes.sessions import (
    _is_favorited,
    _main_message_count,
    _parse_projects_param,
    _summary,
    _user_title,
)
from introspect.models import (
    ArchivedSession,
    ChatSession,
    ContentBlock,
    Message,
    Project,
    Transcript,
)
from introspect.search import SearchHit, get_search_index
from introspect.search.fts5 import SOURCES_ALL

router = APIRouter(prefix="/api/v1")

_GROUP_CAP = 5

#: Default sources: the human<->Claude dialogue only (spec 2026-08-15 — the "mainly for
#: Claude" read APIs trim to the chat; the room's client widens explicitly with sources=all).
_DEFAULT_SOURCES = frozenset({"chat"})


def _parse_sources_param(raw: str | None) -> frozenset[str] | JSONResponse:
    """Comma-separated tokens from {chat, agents, system, all} -> the index's sources set.

    ``None`` -> the chat default. ``all`` expands to every bucket. An unknown token is a 422
    problem, never silently ignored (spec §4: no silent filter surprises).
    """
    if raw is None:
        return _DEFAULT_SOURCES
    selected: set[str] = set()
    for token in (t.strip().lower() for t in raw.split(",") if t.strip()):
        if token == "all":
            selected |= SOURCES_ALL
        elif token in SOURCES_ALL:
            selected.add(token)
        else:
            return _problem(
                f"unknown source '{token}' -- valid: chat, agents, system, all"
            )
    return frozenset(selected) if selected else _DEFAULT_SOURCES


# --- Response envelopes (route-local; HitOut/SessionSummary live in api.models) ----------


class SearchGroup(BaseModel):
    session: SessionSummary
    hits: list[HitOut]
    has_more: bool


class GlobalSearchResult(BaseModel):
    groups: list[SearchGroup]
    total: int


class SessionSearchResult(BaseModel):
    items: list[HitOut]
    total: int


# --- Helpers --------------------------------------------------------------------------


def _problem(detail: str) -> JSONResponse:
    """A 422 problem response for the two request-shape errors this route rejects inline."""
    problem = Problem(status=422, title=HTTPStatus.UNPROCESSABLE_ENTITY.phrase, detail=detail)
    return JSONResponse(status_code=422, content=problem.model_dump())


def _drop_archived_hits(db: Session, hits: list[SearchHit]) -> list[SearchHit]:
    """Filter out hits whose owning session is archived (§15.1), post-index, at the route.

    The spec keeps all FTS SQL behind the ``SearchIndex`` boundary (critique #1 -- protects the
    Postgres promise), so archived-exclusion for search is a route-level post-filter over the
    returned hits rather than a predicate pushed into ``content_fts MATCH``: ONE ``IN`` query
    resolves which of the page's distinct session uuids are archived, then those hits are dropped.
    ``total`` (from ``SearchIndex.search``) is left as the index reported it -- it counts pre-page
    matches for the query and is not re-derived per page; archived sessions are rare and expected
    to be hidden, so the small over-count on a page that happened to include archived hits is the
    accepted cost of not threading archive state through the index (brief: post-filter at route).
    """
    if not hits:
        return hits
    session_uuids = {hit.session_uuid for hit in hits}
    archived = set(
        db.execute(
            select(ArchivedSession.session_uuid).where(
                ArchivedSession.session_uuid.in_(session_uuids)
            )
        ).scalars()
    )
    if not archived:
        return hits
    return [hit for hit in hits if hit.session_uuid not in archived]


# --- Id-shaped `q` bypass (Task T3): exact-match on Message.api_message_id --------------

# Conservative and case-sensitive: only a bare "msg_" + alnum id counts as id-shaped -- a
# phrase merely containing "msg_" (a space, extra punctuation, or nothing after the prefix)
# falls through to the normal FTS path instead.
_ID_SHAPED_QUERY_RE = re.compile(r"^msg_[A-Za-z0-9]+$")

#: Matches the FTS5 ``snippet()`` call's own max-token count (see ``search/fts5.py``'s
#: ``_SELECT_SQL``), so an id-lookup preview is sized the same as an FTS snippet.
_ID_LOOKUP_SNIPPET_MAX_WORDS = 12


def _truncate_id_lookup_snippet(text: str) -> str:
    """First ``_ID_LOOKUP_SNIPPET_MAX_WORDS`` words of ``text``, ellipsis-suffixed if cut."""
    words = text.split()
    if len(words) <= _ID_LOOKUP_SNIPPET_MAX_WORDS:
        return text
    return " ".join(words[:_ID_LOOKUP_SNIPPET_MAX_WORDS]) + " …"


def _first_blocks_by_message(
    db: Session, message_ids: list[int]
) -> tuple[dict[int, tuple[int, int, str, str]], dict[int, tuple[int, int, str, str | None]]]:
    """For each message id, its first non-empty TEXT block and (as a fallback) its first
    block of any kind -- both keyed by ``message_id``, both a ``(block_id, block_index,
    block_kind, text_content)`` tuple.

    ONE batched query, ordered by ``block_index``, walked once to pick each message's
    earliest matching block of each kind -- no second round trip. The text-block predicate
    mirrors ``search/fts5.py``'s ``_TEXT_PREDICATE`` (``block_kind == 'text' and
    text_content``), kept independent since this route may not modify that module.
    """
    if not message_ids:
        return {}, {}
    rows = db.execute(
        select(
            ContentBlock.message_id,
            ContentBlock.id,
            ContentBlock.block_index,
            ContentBlock.block_kind,
            ContentBlock.text_content,
        )
        .where(ContentBlock.message_id.in_(message_ids))
        .order_by(ContentBlock.message_id, ContentBlock.block_index)
    ).all()
    text_block: dict[int, tuple[int, int, str, str]] = {}
    fallback_block: dict[int, tuple[int, int, str, str | None]] = {}
    for message_id, block_id, block_index, block_kind, text_content in rows:
        fallback_block.setdefault(message_id, (block_id, block_index, block_kind, text_content))
        if message_id not in text_block and block_kind == "text" and text_content:
            text_block[message_id] = (block_id, block_index, block_kind, text_content)
    return text_block, fallback_block


def _api_message_id_query(
    api_message_id: str, *, session_uuid: str | None, project_slugs: list[str] | None
) -> Select[tuple[Message, str]]:
    """The base SQLAlchemy ``Select`` for an exact ``Message.api_message_id`` match.

    Shared by the count and page queries in :func:`_api_message_id_hits` so the two can
    never drift on filters. ``session_uuid`` scopes to one session (session scope);
    ``project_slugs`` narrows by project (global scope only -- mirrors
    ``_project_clauses`` in ``search/fts5.py``: ``None`` = unfiltered, ``[]`` = matches
    nothing).
    """
    stmt = (
        select(Message, Transcript.session_id)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(Message.api_message_id == api_message_id)
    )
    if session_uuid is not None:
        stmt = stmt.where(Transcript.session_id == session_uuid)
    if project_slugs is not None:
        stmt = (
            stmt.join(ChatSession, ChatSession.session_uuid == Transcript.session_id)
            .join(Project, Project.id == ChatSession.project_id)
            .where(Project.dir_slug.in_(project_slugs))
        )
    return stmt


def _api_message_id_hits(
    db: Session,
    api_message_id: str,
    *,
    session_uuid: str | None,
    project_slugs: list[str] | None,
    limit: int,
    offset: int,
) -> tuple[list[SearchHit], int]:
    """Exact-match lookup on ``Message.api_message_id`` -- the id-shaped-``q`` bypass of FTS.

    Mirrors :meth:`SearchIndex.search`'s ``(hits, total)`` contract exactly (the same
    :class:`SearchHit` shape; ``total`` is the pre-page, pre-archived-drop match count, just
    like the FTS path's -- see :func:`_drop_archived_hits`'s accepted-cost note, which applies
    identically here since the SAME post-filter runs over these hits too) so every downstream
    step -- ``_drop_archived_hits``, ``_agent_hex_by_transcript``, ``_hit_out``, ``_group_hits``
    -- runs unchanged regardless of which path produced the hits. Ordered by ascending
    ``Message.id`` (no bm25 rank exists for an exact-match lookup, so insertion order is the
    only meaningful, deterministic order).

    ``sources=`` is deliberately NOT applied here (spec, Task T3): an exact identity lookup on
    one api_message_id is a single message wherever it lives, and filtering it by source kind
    would just make a valid hit vanish for no good reason.
    """
    stmt = _api_message_id_query(
        api_message_id, session_uuid=session_uuid, project_slugs=project_slugs
    )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Message.id.asc()).limit(limit).offset(offset)).all()

    text_block, fallback_block = _first_blocks_by_message(db, [message.id for message, _ in rows])

    hits: list[SearchHit] = []
    for message, session_uuid_val in rows:
        block = text_block.get(message.id)
        if block is not None:
            block_id, block_index, block_kind, text_content = block
            snippet = _truncate_id_lookup_snippet(text_content)
        else:
            fallback = fallback_block.get(message.id)
            block_id, block_index, block_kind = (
                fallback[:3] if fallback is not None else (0, 0, "text")
            )
            snippet = ""
        hits.append(
            SearchHit(
                session_uuid=session_uuid_val,
                transcript_id=message.transcript_id,
                message_id=message.id,
                record_uuid=message.record_uuid,
                block_id=block_id,
                block_index=block_index,
                block_kind=block_kind,
                snippet=snippet,
                rank=0.0,
                timestamp=message.timestamp,
            )
        )
    return hits, int(total)


def _agent_hex_by_transcript(db: Session, hits: list[SearchHit]) -> dict[int, str | None]:
    """Map every hit's ``transcript_id`` to its subagent hex (``None`` for main transcripts).

    ONE ``IN`` query per request, never per-hit: the search index returns bare transcript ids,
    but the reader's deep-link seam routes a hit by whether its transcript is a subagent (so it
    can open the ``/a/{hex}/`` drill-in instead of the main-conversation path). ``kind`` is the
    discriminator -- a main transcript maps to ``None`` even though its ``agent_hex_id`` column
    is likewise null, keeping the contract explicit rather than incidental.
    """
    ids = {hit.transcript_id for hit in hits}
    if not ids:
        return {}
    rows = db.execute(
        select(Transcript.id, Transcript.kind, Transcript.agent_hex_id).where(
            Transcript.id.in_(ids)
        )
    ).all()
    return {tid: (agent_hex if kind == "subagent" else None) for tid, kind, agent_hex in rows}


def _hit_out(hit: SearchHit, agent_hex_by_transcript: dict[int, str | None]) -> HitOut:
    """Build a ``HitOut`` from a ``SearchHit``, injecting the transcript's subagent hex.

    Keeps the ``model_validate`` pattern (see :class:`HitOut`); ``agent_hex_id`` is the only
    field the index doesn't supply, so it is set from the per-request lookup via ``model_copy``.
    """
    return HitOut.model_validate(hit).model_copy(
        update={"agent_hex_id": agent_hex_by_transcript.get(hit.transcript_id)}
    )


def _session_summary(db: Session, session_uuid: str) -> SessionSummary:
    """Build one SessionSummary via the same query shape ``sessions.get_session`` uses."""
    session, slug, count, fav, u_title = db.execute(
        select(
            ChatSession, Project.dir_slug, _main_message_count(), _is_favorited(), _user_title()
        )
        .join(Project, ChatSession.project_id == Project.id)
        .where(ChatSession.session_uuid == session_uuid)
    ).one()
    return _summary(session, slug, count, fav, u_title)


def _group_hits(
    db: Session, hits: list[SearchHit], agent_hex_by_transcript: dict[int, str | None]
) -> list[SearchGroup]:
    """Partition one rank-ordered hit page into per-session groups, best rank first.

    A session's first appearance while walking the (already bm25-ascending) hit list is
    necessarily its best-ranked hit in the page, so building groups in first-encounter order
    gives best-rank-first for free -- no separate min() pass needed.
    """
    order: list[str] = []
    by_session: dict[str, list[SearchHit]] = {}
    for hit in hits:
        bucket = by_session.setdefault(hit.session_uuid, [])
        if not bucket:
            order.append(hit.session_uuid)
        bucket.append(hit)

    return [
        SearchGroup(
            session=_session_summary(db, session_uuid),
            hits=[
                _hit_out(h, agent_hex_by_transcript)
                for h in by_session[session_uuid][:_GROUP_CAP]
            ],
            has_more=len(by_session[session_uuid]) > _GROUP_CAP,
        )
        for session_uuid in order
    ]


# --- Endpoint -------------------------------------------------------------------------


@router.get("/search", response_model=GlobalSearchResult | SessionSearchResult)
def search(
    q: str,
    db: Session = Depends(get_db),
    scope: Literal["global", "session"] = "global",
    session: str | None = None,
    projects: str | None = None,
    sources: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> GlobalSearchResult | SessionSearchResult | JSONResponse:
    if not q.strip():
        return _problem("q must not be empty")
    if scope == "session" and not session:
        return _problem("session is required when scope=session")
    source_set = _parse_sources_param(sources)
    if isinstance(source_set, JSONResponse):
        return source_set

    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)

    index = get_search_index()
    id_shaped = _ID_SHAPED_QUERY_RE.match(q) is not None

    if scope == "session":
        # `projects=` is accepted and explicitly IGNORED here (spec critique #7): threading it
        # into a session-scope search would risk filtering out the very session being read, so
        # this scope never passes project_slugs to the index -- unlike global scope below.
        if id_shaped:
            hits, total = _api_message_id_hits(
                db, q, session_uuid=session, project_slugs=None, limit=limit, offset=offset
            )
        else:
            hits, total = index.search(
                db, q, session_uuid=session, sources=source_set, limit=limit, offset=offset
            )
        hits = _drop_archived_hits(db, hits)
        agent_hex = _agent_hex_by_transcript(db, hits)
        return SessionSearchResult(
            items=[_hit_out(h, agent_hex) for h in hits], total=total
        )

    project_slugs = _parse_projects_param(projects)
    if id_shaped:
        hits, total = _api_message_id_hits(
            db, q, session_uuid=None, project_slugs=project_slugs, limit=limit, offset=offset
        )
    else:
        hits, total = index.search(
            db, q, project_slugs=project_slugs, sources=source_set, limit=limit, offset=offset
        )
    hits = _drop_archived_hits(db, hits)
    agent_hex = _agent_hex_by_transcript(db, hits)
    return GlobalSearchResult(groups=_group_hits(db, hits, agent_hex), total=total)
