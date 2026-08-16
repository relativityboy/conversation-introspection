"""In-process TUI search unit tests (§16), against the pinned fixture archive."""

from __future__ import annotations

from sqlalchemy.orm import Session

from introspect.ingest.capture import utcnow
from introspect.models import ArchivedSession, UserTitle
from introspect.tui.search import (
    clean_snippet,
    display_title,
    parse_source_flags,
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
    # "cormorant" lives only in a subagent transcript whose parent is session 1 -- outside
    # the default chat sources (spec 2026-08-15), so finding it requires widening.
    rows = search_sessions(
        indexed_fixture, "cormorant", sources=frozenset({"chat", "agents"})
    )
    assert len(rows) == 1
    assert rows[0].session_uuid == SESSION_UUID_1
    assert rows[0].record_uuid is not None


def test_search_defaults_to_chat_sources(indexed_fixture: Session) -> None:
    # Default = the human<->Claude dialogue only: subagent content is invisible until the
    # search widens with a flag (the ratified chat-by-default trim, spec 2026-08-15).
    assert search_sessions(indexed_fixture, "cormorant") == []


def test_search_main_hit_has_no_agent_hex(indexed_fixture: Session) -> None:
    # "horizon" lives in the MAIN transcript -> the deep link must route to /m/, not /a/.
    rows = search_sessions(indexed_fixture, "horizon")
    assert rows[0].agent_hex_id is None


def test_search_subagent_hit_carries_agent_hex(indexed_fixture: Session) -> None:
    # "cormorant"'s best hit is in the subagent transcript -> its hex must ride along so the
    # Right-arrow link becomes /s/{uuid}/a/{hex}/m/{record} (never the main-transcript /m/ that
    # would 404 -- the cross-layer bug the Phase 3 walk caught in the web sidebar).
    rows = search_sessions(
        indexed_fixture, "cormorant", sources=frozenset({"chat", "agents"})
    )
    assert rows[0].agent_hex_id == AGENT_HEX_ID


# --- Source flags in the search text (spec 2026-08-15) ------------------------------------


def test_parse_source_flags_default_is_chat() -> None:
    assert parse_source_flags("horizon band") == ("horizon band", frozenset({"chat"}))


def test_parse_source_flags_widen_additively() -> None:
    assert parse_source_flags("horizon --agents") == (
        "horizon", frozenset({"chat", "agents"})
    )
    assert parse_source_flags("--system horizon") == (
        "horizon", frozenset({"chat", "system"})
    )
    assert parse_source_flags("horizon --agents --system") == (
        "horizon", frozenset({"chat", "agents", "system"})
    )


def test_parse_source_flags_all() -> None:
    assert parse_source_flags("cormorant --all") == (
        "cormorant", frozenset({"chat", "agents", "system"})
    )


def test_parse_source_flags_unknown_flag_stays_literal() -> None:
    # An unrecognized --token is treated as search text, never silently swallowed.
    assert parse_source_flags("horizon --bogus") == (
        "horizon --bogus", frozenset({"chat"})
    )
