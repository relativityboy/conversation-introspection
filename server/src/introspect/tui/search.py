"""In-process archive search for the TUI's default (non-slash) input (§16).

Mirrors the web ``/search`` route's read semantics WITHOUT HTTP: one rank-ordered global FTS
pass via :func:`introspect.search.get_search_index`, archived sessions excluded (§15.1 applies
to the TUI -- it is a read path), then grouped to one row per session carrying that session's
best hit (lowest bm25 rank) for the snippet and the deep-link ``record_uuid``. Title display
uses the §14.3 precedence ``user > ai > custom > uuid-prefix``, identical to the web reader.

No ``textual`` import here -- this is a plain query layer the app calls; keeping it textual-free
makes it unit-testable against the standard fixture archive without a running app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.models import ArchivedSession, ChatSession, Project, Transcript, UserTitle
from introspect.search import get_search_index

# FTS5 snippets arrive wrapped in <mark>...</mark> with `…` ellipses; the terminal list wants a
# single clean line, so strip the marks and collapse whitespace (newlines included).
_MARK_RE = re.compile(r"</?mark>")
_WS_RE = re.compile(r"\s+")

#: The default sources for TUI search: the human<->Claude dialogue only (spec 2026-08-15).
CHAT_SOURCES = frozenset({"chat"})
_FLAG_TO_SOURCES = {
    "--agents": frozenset({"agents"}),
    "--system": frozenset({"system"}),
    "--all": frozenset({"agents", "system"}),
}


def parse_source_flags(raw: str) -> tuple[str, frozenset[str]]:
    """Split widen-flags out of a search line: ``("cormorant --agents", ...)`` ->
    ``("cormorant", {"chat", "agents"})``.

    Flags are additive over the chat default (spec 2026-08-15: "other sources addable as
    flags"). An unrecognized ``--token`` stays in the query text — treated as literal search
    input, never silently swallowed.
    """
    sources = CHAT_SOURCES
    kept: list[str] = []
    for word in raw.split():
        extra = _FLAG_TO_SOURCES.get(word)
        if extra is not None:
            sources = sources | extra
        else:
            kept.append(word)
    return " ".join(kept), sources


def display_title(
    user_title: str | None,
    ai_title: str | None,
    custom_title: str | None,
    session_uuid: str,
) -> str:
    """The §14.3 title precedence, in Python for the TUI: user > ai > custom > uuid-prefix.

    A whitespace-only candidate is skipped (treated as absent), matching how the web reader
    falls through empties. The final fallback is the session uuid's 8-char prefix.
    """
    for candidate in (user_title, ai_title, custom_title):
        if candidate and candidate.strip():
            return candidate
    return session_uuid[:8]


def clean_snippet(snippet: str) -> str:
    """Strip FTS ``<mark>`` tags and collapse whitespace to one line for terminal display."""
    return _WS_RE.sub(" ", _MARK_RE.sub("", snippet)).strip()


@dataclass
class SearchResultRow:
    """One navigable search result: a session plus its best hit's deep-link + snippet."""

    session_uuid: str
    title: str
    project_slug: str
    date: datetime | None
    snippet: str
    record_uuid: str | None  # best hit's record; None only for the defensive thin-record case
    #: The best hit's subagent hex when it lives in a SUBAGENT transcript, else None. Routes the
    #: Right-arrow deep link (:func:`introspect.tui.webserver.right_arrow_url`): a subagent hit
    #: must open the /a/{hex}/m/ drill-in, never the main-transcript /m/ (which would 404).
    agent_hex_id: str | None


def search_sessions(
    db: Session,
    query: str,
    *,
    limit: int = 50,
    sources: frozenset[str] = CHAT_SOURCES,
) -> list[SearchResultRow]:
    """Rank-ordered, archived-excluded, one-row-per-session results for ``query``.

    ``limit`` bounds the underlying HIT page (as the web route does); sessions are grouped from
    those hits, so a session's row carries its best-ranked hit within that page. An empty or
    no-match query returns ``[]`` without further DB work. ``sources`` defaults to the chat
    bucket — the human<->Claude dialogue — per the 2026-08-15 ruling; callers widen via
    :func:`parse_source_flags`.
    """
    hits, _total = get_search_index().search(db, query, limit=limit, sources=sources)
    if not hits:
        return []

    # First appearance of a session while walking the bm25-ascending hit list IS its best hit
    # (same invariant the search route's `_group_hits` relies on).
    order: list[str] = []
    best_by_session = {}
    for hit in hits:
        if hit.session_uuid not in best_by_session:
            order.append(hit.session_uuid)
            best_by_session[hit.session_uuid] = hit

    archived = set(
        db.execute(
            select(ArchivedSession.session_uuid).where(
                ArchivedSession.session_uuid.in_(order)
            )
        ).scalars()
    )
    visible = [uuid for uuid in order if uuid not in archived]
    if not visible:
        return []

    meta = {
        session.session_uuid: (session, slug, user_title)
        for (session, slug, user_title) in db.execute(
            select(ChatSession, Project.dir_slug, UserTitle.title)
            .join(Project, ChatSession.project_id == Project.id)
            .outerjoin(UserTitle, UserTitle.session_uuid == ChatSession.session_uuid)
            .where(ChatSession.session_uuid.in_(visible))
        ).all()
    }

    # Map each best hit's transcript to its subagent hex (None for main transcripts) in ONE IN
    # query, mirroring routes/search.py `_agent_hex_by_transcript`: `kind` is the discriminator,
    # so a main transcript maps to None even though its agent_hex_id column is likewise null. This
    # is what lets the Right-arrow link route a subagent hit to /a/{hex}/m/ instead of the main
    # /m/ that would 404 (the cross-layer bug the Phase 3 walk caught in the web sidebar).
    best_transcript_ids = {best_by_session[uuid].transcript_id for uuid in visible}
    agent_hex_by_transcript = {
        tid: (agent_hex if kind == "subagent" else None)
        for tid, kind, agent_hex in db.execute(
            select(Transcript.id, Transcript.kind, Transcript.agent_hex_id).where(
                Transcript.id.in_(best_transcript_ids)
            )
        ).all()
    }

    rows: list[SearchResultRow] = []
    for uuid in visible:
        entry = meta.get(uuid)
        if entry is None:  # hit referenced a session with no row -- skip defensively
            continue
        session, slug, user_title = entry
        hit = best_by_session[uuid]
        rows.append(
            SearchResultRow(
                session_uuid=uuid,
                title=display_title(user_title, session.ai_title, session.custom_title, uuid),
                project_slug=slug,
                date=session.last_activity_at or session.started_at,
                snippet=clean_snippet(hit.snippet),
                record_uuid=hit.record_uuid,
                agent_hex_id=agent_hex_by_transcript.get(hit.transcript_id),
            )
        )
    return rows
