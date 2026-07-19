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

Empty/whitespace ``q`` and a missing ``session`` under ``scope=session`` are the only two
request-shape errors this route rejects, both as inline 422 problem responses (see
:func:`_problem`) rather than through ``RequestValidationError`` -- simpler than constructing
a synthetic validation-error payload, and the resulting JSON is the same ``{status, title,
detail}`` shape :mod:`introspect.api.errors` produces. No other input can fail:
:func:`introspect.search.sanitize_query` guarantees every ``q`` sanitizes to a syntactically
valid (possibly empty-match) FTS5 query, so nothing here wraps ``search()`` in a try/except --
doing so would risk masking a real corruption error as an empty result, which the sanitizer's
never-raise guarantee is specifically meant to make unnecessary.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import _DEFAULT_LIMIT, _MAX_LIMIT, HitOut, Problem, SessionSummary
from introspect.api.routes.sessions import _is_favorited, _main_message_count, _summary
from introspect.models import ChatSession, Project, Transcript
from introspect.search import SearchHit, get_search_index

router = APIRouter(prefix="/api/v1")

_GROUP_CAP = 5


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
    session, slug, count, fav = db.execute(
        select(ChatSession, Project.dir_slug, _main_message_count(), _is_favorited())
        .join(Project, ChatSession.project_id == Project.id)
        .where(ChatSession.session_uuid == session_uuid)
    ).one()
    return _summary(session, slug, count, fav)


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
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> GlobalSearchResult | SessionSearchResult | JSONResponse:
    if not q.strip():
        return _problem("q must not be empty")
    if scope == "session" and not session:
        return _problem("session is required when scope=session")

    limit = min(max(limit, 1), _MAX_LIMIT)
    offset = max(offset, 0)

    index = get_search_index()

    if scope == "session":
        hits, total = index.search(db, q, session_uuid=session, limit=limit, offset=offset)
        agent_hex = _agent_hex_by_transcript(db, hits)
        return SessionSearchResult(
            items=[_hit_out(h, agent_hex) for h in hits], total=total
        )

    hits, total = index.search(db, q, limit=limit, offset=offset)
    agent_hex = _agent_hex_by_transcript(db, hits)
    return GlobalSearchResult(groups=_group_hits(db, hits, agent_hex), total=total)
