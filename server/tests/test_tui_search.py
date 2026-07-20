"""In-process TUI search unit tests (§16), against the pinned fixture archive."""

from __future__ import annotations

from sqlalchemy.orm import Session

from introspect.ingest.capture import utcnow
from introspect.models import ArchivedSession, UserTitle
from introspect.tui.search import (
    clean_snippet,
    display_title,
    search_sessions,
)
from tests.conftest import AGENT_HEX_ID, SESSION_UUID_1


def test_display_title_precedence() -> None:
    # user > ai > custom > uuid-prefix
    assert display_title("u", "a", "c", "1234567890") == "u"
    assert display_title(None, "a", "c", "1234567890") == "a"
    assert display_title("   ", "a", "c", "1234567890") == "a"  # whitespace-only skipped
    assert display_title(None, None, "c", "1234567890") == "c"
    assert display_title(None, None, None, "1234567890") == "12345678"


def test_clean_snippet_strips_marks_and_collapses_whitespace() -> None:
    assert clean_snippet("the <mark>horizon</mark>\n band") == "the horizon band"


def test_search_returns_navigable_rows(indexed_fixture: Session) -> None:
    # "horizon" is a single-occurrence phrase living only in session 1's MAIN transcript.
    rows = search_sessions(indexed_fixture, "horizon")
    assert len(rows) == 1
    row = rows[0]
    assert row.session_uuid == SESSION_UUID_1
    assert row.project_slug  # a real slug, not empty
    assert row.record_uuid is not None  # the deep-link target for Right-arrow
    assert "horizon" in row.snippet
    assert "<mark>" not in row.snippet  # marks stripped for the terminal


def test_search_uses_user_title_when_present(indexed_fixture: Session) -> None:
    indexed_fixture.add(
        UserTitle(session_uuid=SESSION_UUID_1, title="My Renamed Session", updated_at=utcnow())
    )
    indexed_fixture.commit()
    rows = search_sessions(indexed_fixture, "horizon")
    assert rows[0].title == "My Renamed Session"


def test_search_excludes_archived_sessions(indexed_fixture: Session) -> None:
    indexed_fixture.add(
        ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow())
    )
    indexed_fixture.commit()
    # The only "horizon" match is session 1, now archived -> no results (§15.1 read-path).
    assert search_sessions(indexed_fixture, "horizon") == []


def test_search_no_match_returns_empty(indexed_fixture: Session) -> None:
    assert search_sessions(indexed_fixture, "zzzznotarealword") == []


def test_search_surfaces_subagent_content_under_parent_session(
    indexed_fixture: Session,
) -> None:
    # "cormorant" lives only in a subagent transcript whose parent is session 1.
    rows = search_sessions(indexed_fixture, "cormorant")
    assert len(rows) == 1
    assert rows[0].session_uuid == SESSION_UUID_1
    assert rows[0].record_uuid is not None


def test_search_main_hit_has_no_agent_hex(indexed_fixture: Session) -> None:
    # "horizon" lives in the MAIN transcript -> the deep link must route to /m/, not /a/.
    rows = search_sessions(indexed_fixture, "horizon")
    assert rows[0].agent_hex_id is None


def test_search_subagent_hit_carries_agent_hex(indexed_fixture: Session) -> None:
    # "cormorant"'s best hit is in the subagent transcript -> its hex must ride along so the
    # Right-arrow link becomes /s/{uuid}/a/{hex}/m/{record} (never the main-transcript /m/ that
    # would 404 -- the cross-layer bug the Phase 3 walk caught in the web sidebar).
    rows = search_sessions(indexed_fixture, "cormorant")
    assert rows[0].agent_hex_id == AGENT_HEX_ID
