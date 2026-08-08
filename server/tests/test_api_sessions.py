"""Read endpoints (Task P2-5): projects, sessions list/detail, transcript messages.

These exercise the real query layer against a populated archive. The app under test is
pointed at the SAME SQLite file the ``db_session`` fixture writes to (``tmp_path/archive.db``),
so a test can stage rows through ``db_session`` (capture the pinned tree, insert a Favorite,
adjust a timestamp), commit, and then read them back over HTTP -- the route handlers open
their own request-scoped sessions via ``get_db`` and see the committed data (WAL cross-conn).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.interpret import classify_pending
from introspect.models import ChatSession, Favorite, Message, Transcript
from tests.conftest import (
    AGENT_HEX_ID,
    AGENT_TOOL_USE_ID,
    AGENT_TYPE,
    PROJECT_SLUG_1,
    PROJECT_SLUG_2,
    SESSION_UUID_1,
    SESSION_UUID_2,
    SESSION_UUID_3,
)
from tests.fixtures.records import (
    make_assistant_line,
    make_queued_command_line,
    make_session_file,
    make_system_line,
    make_tool_result_user_line,
    make_user_line,
)


def _capture(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    db.commit()


@pytest.fixture
def client(db_session: Session, fixture_tree: Path, tmp_path: Path) -> TestClient:
    """App over the pinned fixture tree, sharing ``db_session``'s DB file."""
    _capture(db_session, fixture_tree)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


def _uuids(items: list[dict]) -> list[str]:
    return [i["session_uuid"] for i in items]


def _main_transcript_id(db: Session, session_uuid: str) -> int:
    return db.query(Transcript.id).filter(
        Transcript.session_id == session_uuid, Transcript.kind == "main"
    ).scalar()


def _subagent_transcript_id(db: Session, session_uuid: str) -> int:
    return db.query(Transcript.id).filter(
        Transcript.session_id == session_uuid, Transcript.kind == "subagent"
    ).scalar()


# --- Projects ---------------------------------------------------------------------------


def test_projects_lists_session_counts(client: TestClient) -> None:
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    by_slug = {p["dir_slug"]: p for p in resp.json()}
    assert set(by_slug) == {PROJECT_SLUG_1, PROJECT_SLUG_2}
    assert set(by_slug[PROJECT_SLUG_1]) == {"id", "dir_slug", "resolved_cwd", "session_count"}
    assert by_slug[PROJECT_SLUG_1]["session_count"] == 2  # sessions 1 + 2
    assert by_slug[PROJECT_SLUG_2]["session_count"] == 1  # session 3
    assert by_slug[PROJECT_SLUG_1]["resolved_cwd"]  # populated from the transcript envelope


def test_projects_session_counts_exclude_archived(client: TestClient) -> None:
    """The tree's count badge must agree with the children a project shows (spec §6.1):
    an archived session vanishes from the sessions list, so it must not be counted."""
    before = {p["dir_slug"]: p["session_count"] for p in client.get("/api/v1/projects").json()}
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    assert resp.status_code == 204
    after = {p["dir_slug"]: p["session_count"] for p in client.get("/api/v1/projects").json()}
    assert after[PROJECT_SLUG_1] == before[PROJECT_SLUG_1] - 1
    # Other projects untouched.
    for slug, count in after.items():
        if slug != PROJECT_SLUG_1:
            assert count == before[slug]


def test_projects_archiving_the_sole_session_keeps_the_project_at_zero(
    client: TestClient,
) -> None:
    """Pins the ON-vs-WHERE choice in ``_not_archived()``'s use inside ``list_projects``' outer
    join: archiving PROJECT_SLUG_2's only session (SESSION_UUID_3) must leave the project listed
    at ``session_count`` 0, not drop the row entirely. A WHERE-clause "simplification" (moving the
    exclusion out of the join's ON and onto the query as a whole) would turn the outer join into
    an inner one in effect for this project, and the row would vanish -- exactly the regression
    this test exists to catch.
    """
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_3}/archive")
    assert resp.status_code == 204

    by_slug = {p["dir_slug"]: p for p in client.get("/api/v1/projects").json()}
    assert PROJECT_SLUG_2 in by_slug
    assert by_slug[PROJECT_SLUG_2]["session_count"] == 0


# --- Sessions list ----------------------------------------------------------------------


def test_sessions_list_orders_desc_nulls_last(db_session: Session, client: TestClient) -> None:
    # All three fixture sessions share one timestamp; spread them out and null one so the
    # DESC-NULLS-LAST ordering is observable.
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_1).update(
        {ChatSession.last_activity_at: datetime(2026, 1, 3, tzinfo=timezone.utc)}
    )
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_2).update(
        {ChatSession.last_activity_at: datetime(2026, 1, 2, tzinfo=timezone.utc)}
    )
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_3).update(
        {ChatSession.last_activity_at: None}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions").json()
    assert body["total"] == 3
    assert _uuids(body["items"]) == [SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3]
    # Summary carries derived fields, not just ORM columns.
    first = body["items"][0]
    assert first["message_count"] == 2  # main transcript of session 1
    assert first["favorite"] is False
    assert first["project_slug"] == PROJECT_SLUG_1


def test_sessions_q_matches_ai_and_custom_title_case_insensitive(
    db_session: Session, client: TestClient
) -> None:
    # (Rewrite of the removed title= test against q=; coverage kept, param gone.)
    # Session 1 already carries ai_title "Synthetic Session Title"; give session 2 a matching
    # custom_title so one case-insensitive substring must hit BOTH columns. "session" is a
    # title-only token here -- it appears in no transcript CONTENT, so this exercises the LIKE
    # disjunct in isolation from the FTS disjunct.
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_2).update(
        {ChatSession.custom_title: "custom session marker"}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "SESSION"}).json()
    assert set(_uuids(body["items"])) == {SESSION_UUID_1, SESSION_UUID_2}
    assert body["total"] == 2
    # Session 3 (no matching title, no matching content) is excluded.
    assert SESSION_UUID_3 not in _uuids(body["items"])
    # Title matches carry no snippet.
    assert all(i["match_snippet"] is None for i in body["items"])


def test_sessions_title_param_no_longer_filters(client: TestClient) -> None:
    # Zero-legacy ruling: title= is REMOVED, not aliased. FastAPI ignores the now-unknown
    # param, so the list comes back UNFILTERED (all three fixture sessions).
    body = client.get("/api/v1/sessions", params={"title": "SYNTHETIC"}).json()
    assert body["total"] == 3
    assert set(_uuids(body["items"])) == {SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3}


def test_sessions_q_empty_or_absent_is_unfiltered(client: TestClient) -> None:
    absent = client.get("/api/v1/sessions").json()
    empty = client.get("/api/v1/sessions", params={"q": ""}).json()
    assert absent["total"] == empty["total"] == 3
    assert set(_uuids(empty["items"])) == {SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3}


def test_sessions_q_matches_uuid_substring_case_insensitive(
    db_session: Session, client: TestClient
) -> None:
    # Insert a session whose uuid carries hex LETTERS so case-insensitivity is observable
    # (the fixture uuids are all-numeric). No transcripts => it can only match by uuid.
    alpha_uuid = "A1B2C3D4-DEAD-BEEF-CAFE-000000000000"
    db_session.add(ChatSession(session_uuid=alpha_uuid, project_id=1))
    db_session.commit()

    # Mixed-case needle against the upper-case stored uuid.
    body = client.get("/api/v1/sessions", params={"q": "a1b2C3d4"}).json()
    assert _uuids(body["items"]) == [alpha_uuid]
    assert body["total"] == 1
    # A uuid match is not a content match -> no snippet.
    assert body["items"][0]["match_snippet"] is None


def test_sessions_q_matches_user_title(db_session: Session, client: TestClient) -> None:
    from introspect.models import UserTitle

    # A user-set title (LEFT JOIN column) with a token present in no archive title or content.
    db_session.add(
        UserTitle(
            session_uuid=SESSION_UUID_3,
            title="renamed adventure log",
            updated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "ADVENTURE"}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_3]
    assert body["total"] == 1
    assert body["items"][0]["match_snippet"] is None


def test_sessions_q_archive_title_not_shadowed_by_user_rename(
    db_session: Session, client: TestClient
) -> None:
    # Critique #5: a user rename must NOT shadow an archive-title match. Session 1's ai_title
    # is "Synthetic Session Title"; the user renames it to something that does NOT contain the
    # query. The archive title must still match (OR across columns, never COALESCE).
    from introspect.models import UserTitle

    db_session.add(
        UserTitle(
            session_uuid=SESSION_UUID_1,
            title="totally different name",
            updated_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "Session"}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]  # matched via ai_title, not shadowed
    assert body["items"][0]["match_snippet"] is None


def test_sessions_q_content_only_match_populates_snippet(client: TestClient) -> None:
    # "horizon" lives only in session 1's MAIN content and in no title/uuid.
    body = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]
    assert body["total"] == 1
    item = body["items"][0]
    snippet = item["match_snippet"]
    assert snippet is not None and "<mark>" in snippet
    # The snippet carries WHERE it matched so the sidebar can deep-link the click. "horizon" is a
    # MAIN-transcript hit: record_uuid points at the matched message, agent_hex_id stays null.
    assert item["match_record_uuid"] is not None
    assert item["match_agent_hex_id"] is None


def test_sessions_q_subagent_content_match_carries_agent_hex(client: TestClient) -> None:
    # "cormorant" lives only in session 1's SUBAGENT transcript (conftest). The content match
    # attributes to the subagent, so the sidebar deep-link must route through /a/{hex}/.
    body = client.get("/api/v1/sessions", params={"q": "cormorant"}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]
    item = body["items"][0]
    assert item["match_snippet"] is not None and "<mark>" in item["match_snippet"]
    assert item["match_record_uuid"] is not None
    assert item["match_agent_hex_id"] == AGENT_HEX_ID


def test_sessions_q_title_match_location_fields_null(
    db_session: Session, client: TestClient
) -> None:
    # A title/uuid match carries no snippet, and the location pair follows the SAME attribution
    # rule: match_record_uuid and match_agent_hex_id are null whenever match_snippet is null.
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_1).update(
        {ChatSession.custom_title: "horizon overview"}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    item = body["items"][0]
    assert item["match_snippet"] is None
    assert item["match_record_uuid"] is None
    assert item["match_agent_hex_id"] is None


def test_sessions_q_title_match_snippet_null_even_when_content_matches(
    db_session: Session, client: TestClient
) -> None:
    # Give session 1 a title containing "horizon" so it matches by BOTH title and content.
    # Title attribution wins -> snippet null, and the row appears exactly ONCE (no OR-dup).
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_1).update(
        {ChatSession.custom_title: "horizon overview"}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]  # exactly one row
    assert body["total"] == 1
    assert body["items"][0]["match_snippet"] is None


@pytest.mark.parametrize(
    "needle,literal_title,non_matching_title",
    [
        # "_" is LIKE's single-char wildcard -- an unescaped '_' would match the 'b' in "abc".
        ("a_c", "alpha a_c omega", "alpha abc omega"),
        # "%" is LIKE's multi-char wildcard -- an unescaped '%' would match "aXYZc" too.
        ("a%c", "alpha a%c omega", "alpha aXYZc omega"),
        # "\" is the ESCAPE char itself -- a literal backslash in q must not be interpreted as
        # introducing an escape sequence for the char that follows it.
        ("a\\c", "alpha a\\c omega", "alpha abc omega"),
    ],
)
def test_sessions_q_wildcard_chars_treated_literally(
    db_session: Session,
    client: TestClient,
    needle: str,
    literal_title: str,
    non_matching_title: str,
) -> None:
    # LIKE metacharacters in q must be literal, not wildcards, and must never crash the query.
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_1).update(
        {ChatSession.custom_title: literal_title}
    )
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_2).update(
        {ChatSession.custom_title: non_matching_title}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": needle}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]
    assert SESSION_UUID_2 not in _uuids(body["items"])
    assert body["total"] == 1


def test_sessions_q_ordering_preserved_when_union_mixes_title_and_content(
    db_session: Session, client: TestClient
) -> None:
    # Union of a content match (session 1, "horizon" in body) and a title match (session 3,
    # custom_title). The three-key DESC-NULLS-LAST ordering must hold across the mixed union,
    # and per-row attribution must still be correct (content -> snippet, title -> null).
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_1).update(
        {ChatSession.last_activity_at: datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_3).update(
        {
            ChatSession.custom_title: "horizon report",
            ChatSession.last_activity_at: datetime(2026, 1, 3, tzinfo=timezone.utc),
        }
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"q": "horizon", "limit": 1}).json()
    # total counts the whole union BEFORE limit; page is clamped to 1.
    assert body["total"] == 2
    assert len(body["items"]) == 1
    # Newest first: session 3 (2026-01-03) leads.
    assert body["items"][0]["session_uuid"] == SESSION_UUID_3
    assert body["items"][0]["match_snippet"] is None  # title match

    full = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    assert _uuids(full["items"]) == [SESSION_UUID_3, SESSION_UUID_1]
    by_uuid = {i["session_uuid"]: i for i in full["items"]}
    assert by_uuid[SESSION_UUID_3]["match_snippet"] is None
    assert "<mark>" in by_uuid[SESSION_UUID_1]["match_snippet"]


def test_sessions_favorite_filter(db_session: Session, client: TestClient) -> None:
    db_session.add(
        Favorite(session_uuid=SESSION_UUID_1, created_at=datetime.now(timezone.utc))
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"favorite": 1}).json()
    assert _uuids(body["items"]) == [SESSION_UUID_1]
    assert body["total"] == 1
    assert body["items"][0]["favorite"] is True

    # Unfiltered list still reports the favorite bit per session.
    all_body = client.get("/api/v1/sessions").json()
    fav = {i["session_uuid"]: i["favorite"] for i in all_body["items"]}
    assert fav[SESSION_UUID_1] is True
    assert fav[SESSION_UUID_2] is False


def test_sessions_projects_filter(client: TestClient) -> None:
    """``projects=`` (comma list) is the ONLY project filter -- the single ``project=`` param
    is removed (zero-legacy ruling, Task 4). Single-slug coverage carried over from the old
    ``project=`` test, plus a two-slug case proving the comma-list unions projects."""
    only_proj2 = client.get("/api/v1/sessions", params={"projects": PROJECT_SLUG_2}).json()
    assert _uuids(only_proj2["items"]) == [SESSION_UUID_3]
    assert only_proj2["total"] == 1

    proj1 = client.get("/api/v1/sessions", params={"projects": PROJECT_SLUG_1}).json()
    assert set(_uuids(proj1["items"])) == {SESSION_UUID_1, SESSION_UUID_2}

    both = client.get(
        "/api/v1/sessions", params={"projects": f"{PROJECT_SLUG_1},{PROJECT_SLUG_2}"}
    ).json()
    assert set(_uuids(both["items"])) == {SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3}
    assert both["total"] == 3

    # A trailing comma alongside a real slug drops the empty segment rather than erroring or
    # matching nothing.
    trailing_comma = client.get(
        "/api/v1/sessions", params={"projects": f"{PROJECT_SLUG_2},"}
    ).json()
    assert _uuids(trailing_comma["items"]) == [SESSION_UUID_3]
    assert trailing_comma["total"] == 1


def test_sessions_projects_unknown_slug_matches_nothing(client: TestClient) -> None:
    # No error, no validation -- an unrecognized slug simply matches zero sessions.
    body = client.get("/api/v1/sessions", params={"projects": "no-such-slug"}).json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.parametrize("projects_value", [None, "", " , ,", ",,"])
def test_sessions_projects_absent_or_empty_is_unfiltered(
    client: TestClient, projects_value: str | None
) -> None:
    # Absent `projects=` and present-but-empty `?projects=` both mean "no chips selected" ->
    # unfiltered, per the route-level mapping (empty comma-list is treated as absent, not as
    # the SearchIndex protocol's `[]` == matches-nothing). Whitespace-and-commas-only input
    # (" , ," / ",,") strips and filters down to the same all-empty case.
    params = {} if projects_value is None else {"projects": projects_value}
    body = client.get("/api/v1/sessions", params=params).json()
    assert set(_uuids(body["items"])) == {SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3}
    assert body["total"] == 3


def test_sessions_projects_composes_with_q_narrows_content_pass(client: TestClient) -> None:
    # "horizon" content-matches session 1 ONLY, which lives in PROJECT_SLUG_1. Filtering to
    # PROJECT_SLUG_2 must exclude it -- both the outer SQL predicate and the
    # session_uuids_matching() content pass must receive the projects= filter (Task 4
    # composition rule with T3's content search).
    excluded = client.get(
        "/api/v1/sessions", params={"q": "horizon", "projects": PROJECT_SLUG_2}
    ).json()
    assert excluded["items"] == []
    assert excluded["total"] == 0

    included = client.get(
        "/api/v1/sessions", params={"q": "horizon", "projects": PROJECT_SLUG_1}
    ).json()
    assert _uuids(included["items"]) == [SESSION_UUID_1]


def test_sessions_limit_clamped_to_200_and_default_50(
    db_session: Session, client: TestClient
) -> None:
    # Bulk-insert bare sessions (no transcripts needed) to exceed both the default and the cap.
    for i in range(205):
        db_session.add(ChatSession(session_uuid=f"extra-{i:04d}", project_id=1))
    db_session.commit()

    clamped = client.get("/api/v1/sessions", params={"limit": 1000}).json()
    assert clamped["total"] == 208  # 3 fixture + 205 extra
    assert len(clamped["items"]) == 200  # limit clamped down to the max

    default = client.get("/api/v1/sessions").json()
    assert len(default["items"]) == 50  # default page size
    assert default["total"] == 208


def test_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/sessions/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


# --- Session detail ---------------------------------------------------------------------


def test_session_detail_includes_subagent_transcript(client: TestClient) -> None:
    body = client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    assert body["session_uuid"] == SESSION_UUID_1
    assert body["project_slug"] == PROJECT_SLUG_1
    assert body["message_count"] == 2  # main transcript only
    assert body["favorite"] is False

    kinds = {t["kind"] for t in body["transcripts"]}
    assert kinds == {"main", "subagent"}
    subagent = next(t for t in body["transcripts"] if t["kind"] == "subagent")
    assert subagent["agent_hex_id"] == AGENT_HEX_ID
    assert subagent["agent_type"] == AGENT_TYPE
    assert subagent["agent_description"]


def test_subagent_transcript_info_join_key_matches_dispatching_block(
    db_session: Session, client: TestClient
) -> None:
    """The UI's SubagentChip join: a subagent's TranscriptInfo.parent_tool_use_id must
    equal the tool_use_id of some block in the main transcript's messages -- the
    dispatching ``tool_use`` block that spawned it (Task P3-0)."""
    body = client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    subagent = next(t for t in body["transcripts"] if t["kind"] == "subagent")
    assert subagent["parent_tool_use_id"] == AGENT_TOOL_USE_ID

    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    messages = client.get(f"/api/v1/transcripts/{tid}/messages").json()["items"]
    main_tool_use_ids = {
        block["tool_use_id"]
        for message in messages
        for block in message["blocks"]
        if block["tool_use_id"] is not None
    }
    assert subagent["parent_tool_use_id"] in main_tool_use_ids


# --- Transcript messages ----------------------------------------------------------------


def test_messages_paging_totals_and_offset_echo(
    db_session: Session, client: TestClient
) -> None:
    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    body = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"offset": 1, "limit": 1}
    ).json()
    assert body["total"] == 2  # two messages in the main transcript
    assert body["offset"] == 1  # echoes the effective offset used
    assert len(body["items"]) == 1
    # MessageOut carries its ordered content blocks.
    assert body["items"][0]["blocks"]
    assert "block_index" in body["items"][0]["blocks"][0]


def test_subagent_transcript_messages_served(
    db_session: Session, client: TestClient
) -> None:
    tid = _subagent_transcript_id(db_session, SESSION_UUID_1)
    expected = [
        u
        for (u,) in db_session.query(Message.record_uuid)
        .filter(Message.transcript_id == tid)
        .order_by(Message.id)
        .all()
    ]
    body = client.get(f"/api/v1/transcripts/{tid}/messages").json()
    assert body["total"] == 2
    assert [m["record_uuid"] for m in body["items"]] == expected


def test_around_centers_mid_target_and_clamps_early_target(
    db_session: Session, tmp_path: Path
) -> None:
    # A long single-transcript session so `around` centering has room to be non-trivial.
    root = tmp_path / "long_tree"
    session_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    proj = root / "-Users-x-long"
    proj.mkdir(parents=True)
    lines = [
        (make_user_line if i % 2 == 0 else make_assistant_line)(
            text=f"long message {i}", sessionId=session_uuid
        )
        for i in range(12)
    ]
    (proj / f"{session_uuid}.jsonl").write_bytes(make_session_file(lines))
    _capture(db_session, root)

    tid = _main_transcript_id(db_session, session_uuid)
    ordered = [
        u
        for (u,) in db_session.query(Message.record_uuid)
        .filter(Message.transcript_id == tid)
        .order_by(Message.id)
        .all()
    ]
    assert len(ordered) == 12

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    # Mid-file target: ordinal 6, limit 4 -> offset = max(0, 6 - 2) = 4, target in the page.
    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"around": ordered[6], "limit": 4},
    ).json()
    assert mid["offset"] == 4
    assert ordered[6] in [m["record_uuid"] for m in mid["items"]]

    # Early target: ordinal 0 -> offset clamped to 0, target still in the page.
    early = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"around": ordered[0], "limit": 4},
    ).json()
    assert early["offset"] == 0
    assert ordered[0] in [m["record_uuid"] for m in early["items"]]


def test_unknown_transcript_is_404_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/transcripts/999999/messages")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_unknown_around_uuid_is_404_problem(
    db_session: Session, client: TestClient
) -> None:
    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    resp = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"around": "no-such-record-uuid"}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


# --- Transcript messages -- view=chat-harness type filtering (Task 4, authorship spec §5) --
#
# `view=chat-harness` hides `system`-type rows from ALL FOUR query sites in `list_messages`
# (total, around-target resolution, around ordinal count, page fetch) per spec §5 + ledger #1:
# attachments stay IN (`type IN ('user','assistant','attachment')`) -- "pasted things are
# things a human said" -- only `system` rows are hidden. None of these rows are ever
# authorship-classified (this tree is captured via `_capture`, which never calls
# `classify_pending`), so every row's `authorship_kind` stays NULL and `chat-harness`
# degrades to the legacy type+content rule -- the spec's "row-identical to today's toggle-on
# view" note, exercised here. A dedicated ad-hoc tree (own TestClient, like
# `test_around_centers_mid_target_and_clamps_early_target` above) is used rather than the
# pinned `fixture_tree`/`client` fixture, since none of its sessions carry a system- or
# attachment-type MESSAGE row and `TOTAL_FIXTURE_LINES` is a pinned contract other tasks
# hardcode.

_VIEW_HARNESS_SESSION_UUID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

# 14 records: 5 system (hidden under view=chat-harness), 9 kept (4 user, 4 assistant, 1
# attachment). Deliberately NOT a truncation of the raw order -- kept rows land at
# non-contiguous raw indices so filtered total/paging/around-centering are exercised
# against a real re-indexing, not just "drop a prefix/suffix".
_VIEW_HARNESS_TREE_TYPES = [
    "system", "user", "assistant", "system", "user", "assistant",
    "attachment", "system", "user", "assistant", "system", "user",
    "assistant", "system",
]


def _view_harness_line(kind: str, index: int) -> bytes:
    if kind == "system":
        return make_system_line(
            content=f"harness system note {index}", sessionId=_VIEW_HARNESS_SESSION_UUID
        )
    if kind == "attachment":
        # A human-origin queued_command: the ONE attachment shape `AttachmentRecord.blocks()`
        # rescues into a content block (Task 5 refinements spec §4) -- a content-less
        # attachment (e.g. the default deferred_tools_delta furniture) would now correctly
        # trim under view=chat-harness, so the "attachment stays IN" exemplar must carry content.
        return make_queued_command_line(sessionId=_VIEW_HARNESS_SESSION_UUID)
    line_fn = make_user_line if kind == "user" else make_assistant_line
    return line_fn(text=f"harness message {index}", sessionId=_VIEW_HARNESS_SESSION_UUID)


def _build_view_harness_tree(db: Session, tmp_path: Path) -> tuple[int, list[str], list[str]]:
    """Capture ``_VIEW_HARNESS_TREE_TYPES`` as a single main transcript under a fresh tree;
    return ``(transcript_id, record_uuids_in_id_order, types_in_id_order)``."""
    root = tmp_path / "view_harness_tree"
    proj = root / "-Users-x-viewharness"
    proj.mkdir(parents=True)
    lines = [_view_harness_line(kind, i) for i, kind in enumerate(_VIEW_HARNESS_TREE_TYPES)]
    (proj / f"{_VIEW_HARNESS_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))
    _capture(db, root)

    tid = _main_transcript_id(db, _VIEW_HARNESS_SESSION_UUID)
    rows = (
        db.query(Message.record_uuid, Message.type)
        .filter(Message.transcript_id == tid)
        .order_by(Message.id)
        .all()
    )
    return tid, [u for (u, _t) in rows], [t for (_u, t) in rows]


def test_messages_view_chat_harness_filters_totals_and_paging(
    db_session: Session, tmp_path: Path
) -> None:
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    filtered_uuids = [u for u, t in zip(record_uuids, types) if t != "system"]
    assert len(filtered_uuids) == 9  # 14 records minus 5 system rows

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    full = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"view": "chat-harness", "limit": 100}
    ).json()
    assert full["total"] == len(filtered_uuids)
    assert [m["record_uuid"] for m in full["items"]] == filtered_uuids
    assert all(m["type"] != "system" for m in full["items"])
    assert any(m["type"] == "attachment" for m in full["items"])  # attachments stay IN

    page = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat-harness", "offset": 2, "limit": 3},
    ).json()
    assert page["total"] == len(filtered_uuids)
    assert page["offset"] == 2  # echoes the effective offset within the FILTERED set
    assert [m["record_uuid"] for m in page["items"]] == filtered_uuids[2:5]


def test_view_chat_harness_around_centers_within_filtered_set(
    db_session: Session, tmp_path: Path
) -> None:
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    filtered_uuids = [u for u, t in zip(record_uuids, types) if t != "system"]
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    # Mid target: the lone attachment row, ordinal 4 within the filtered set (0-indexed) --
    # system rows sit on both sides of it in the raw order.
    target = record_uuids[6]
    assert types[6] == "attachment"
    assert filtered_uuids.index(target) == 4

    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat-harness", "around": target, "limit": 4},
    ).json()
    assert mid["total"] == len(filtered_uuids)
    assert mid["offset"] == 2  # max(0, 4 - 4 // 2), computed against the filtered ordinal
    assert [m["record_uuid"] for m in mid["items"]] == filtered_uuids[2:6]
    assert target in [m["record_uuid"] for m in mid["items"]]

    # Early target: ordinal 0 within the filtered set -> offset clamps to 0.
    early_target = filtered_uuids[0]
    early = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat-harness", "around": early_target, "limit": 4},
    ).json()
    assert early["offset"] == 0
    assert early_target in [m["record_uuid"] for m in early["items"]]


def test_view_chat_harness_around_system_target_404s_but_found_in_all(
    db_session: Session, tmp_path: Path
) -> None:
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    system_target = record_uuids[0]
    assert types[0] == "system"
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    filtered_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat-harness", "around": system_target},
    )
    assert filtered_resp.status_code == 404
    body = filtered_resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404

    all_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "all", "around": system_target},
    )
    assert all_resp.status_code == 200
    assert system_target in [m["record_uuid"] for m in all_resp.json()["items"]]


def test_messages_view_absent_defaults_to_all(db_session: Session, tmp_path: Path) -> None:
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    body = client.get(f"/api/v1/transcripts/{tid}/messages", params={"limit": 100}).json()
    assert body["total"] == len(record_uuids)
    assert [m["record_uuid"] for m in body["items"]] == record_uuids
    assert any(m["type"] == "system" for m in body["items"])


# --- Transcript messages -- view=chat-harness content-emptiness trim (spec §4/§5) --------
#
# Layered on top of the type filter tested above: `view=chat-harness` additionally hides rows
# whose blocks carry no visible content in conversation mode (tool-only, thinking-only, empty
# text). A dedicated small tree isolates the content dimension from the type dimension already
# covered by `_build_view_harness_tree` above, so this fixture doesn't disturb that tree's
# pinned row-count/ordinal assertions.

_VIEW_TRIM_SESSION_UUID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _trim_control_line() -> bytes:
    return make_assistant_line(
        text="view trim control reply", sessionId=_VIEW_TRIM_SESSION_UUID
    )


def _trim_tool_only_line() -> bytes:
    tuid = "toolu_trim_only"
    return make_assistant_line(
        text="",
        with_tool_use=True,
        tool_use_id=tuid,
        extra_blocks=[
            {
                "type": "tool_result",
                "tool_use_id": tuid,
                "content": "synthetic result",
                "is_error": False,
            }
        ],
        sessionId=_VIEW_TRIM_SESSION_UUID,
    )


def _trim_thinking_only_line() -> bytes:
    return make_assistant_line(
        text="", with_thinking=True, sessionId=_VIEW_TRIM_SESSION_UUID
    )


def _trim_empty_text_line() -> bytes:
    return make_user_line(
        content=[{"type": "text", "text": ""}], sessionId=_VIEW_TRIM_SESSION_UUID
    )


def _trim_unknown_kind_line() -> bytes:
    return make_assistant_line(
        text="",
        extra_blocks=[{"type": "futurekind", "note": "forward-tolerant block"}],
        sessionId=_VIEW_TRIM_SESSION_UUID,
    )


def _trim_image_only_line() -> bytes:
    # `block_kind == "image"` is its OWN OR-branch in `_prose_visible` (sessions.py), separate
    # from the unknown-kind fallback branch -- "image" is a KNOWN kind (`_KNOWN_BLOCK_KINDS`), so
    # without this dedicated branch it would fall neither into the text case nor the unknown-kind
    # case and would trim despite being visible content client-side. No server test exercised this
    # branch before (final review finding 5) -- mirrors the client parity table's
    # 'assistant, image only' case (web/tests/chatOnly.test.ts).
    return make_assistant_line(
        text="",
        extra_blocks=[
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}
        ],
        sessionId=_VIEW_TRIM_SESSION_UUID,
    )


def _build_view_trim_tree(
    db: Session, tmp_path: Path
) -> tuple[int, str, str, str, str, str, str]:
    """Capture one message per Spec §4 emptiness case into a fresh transcript; return
    ``(transcript_id, control_uuid, tool_only_uuid, thinking_only_uuid, empty_text_uuid,
    unknown_kind_uuid, image_only_uuid)`` in insertion (== id) order."""
    root = tmp_path / "view_trim_tree"
    proj = root / "-Users-x-viewtrim"
    proj.mkdir(parents=True)
    lines = [
        _trim_control_line(),
        _trim_tool_only_line(),
        _trim_thinking_only_line(),
        _trim_empty_text_line(),
        _trim_unknown_kind_line(),
        _trim_image_only_line(),
    ]
    (proj / f"{_VIEW_TRIM_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))
    _capture(db, root)

    tid = _main_transcript_id(db, _VIEW_TRIM_SESSION_UUID)
    uuids = [
        u
        for (u,) in db.query(Message.record_uuid)
        .filter(Message.transcript_id == tid)
        .order_by(Message.id)
        .all()
    ]
    (
        control_uuid,
        tool_only_uuid,
        thinking_only_uuid,
        empty_text_uuid,
        unknown_kind_uuid,
        image_only_uuid,
    ) = uuids
    return (
        tid,
        control_uuid,
        tool_only_uuid,
        thinking_only_uuid,
        empty_text_uuid,
        unknown_kind_uuid,
        image_only_uuid,
    )


def test_view_chat_harness_trims_content_empty_rows(db_session: Session, tmp_path: Path) -> None:
    """PARITY PIN: mirrors web/tests/chatOnly.test.ts::trim fixtures — change both together.
    Spec §4: visible iff type qualifies AND ≥1 block shows content in conversation mode
    (non-empty text, image, or unknown kind). tool-only / thinking-only / empty-text rows trim."""
    (
        tid,
        control_uuid,
        tool_only_uuid,
        thinking_only_uuid,
        empty_text_uuid,
        unknown_kind_uuid,
        image_only_uuid,
    ) = _build_view_trim_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    all_rows = client.get(f"/api/v1/transcripts/{tid}/messages").json()
    filtered = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"view": "chat-harness"}
    ).json()
    filtered_uuids = {m["record_uuid"] for m in filtered["items"]}

    assert tool_only_uuid not in filtered_uuids
    assert thinking_only_uuid not in filtered_uuids
    assert empty_text_uuid not in filtered_uuids
    assert unknown_kind_uuid in filtered_uuids
    assert image_only_uuid in filtered_uuids
    assert control_uuid in filtered_uuids
    # totals agree with the trimmed item set, and the unfiltered view is untouched
    assert filtered["total"] == len(filtered_uuids)
    assert {m["record_uuid"] for m in all_rows["items"]} >= {tool_only_uuid, thinking_only_uuid}


def test_view_chat_harness_around_trimmed_target_is_404(
    db_session: Session, tmp_path: Path
) -> None:
    """Deep link into a trimmed row under the filter → 404 (the reader's recovery banner path);
    the same around succeeds under view=all."""
    tid, _control, tool_only_uuid, _thinking, _empty, _unknown, _image = _build_view_trim_tree(
        db_session, tmp_path
    )
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    r = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat-harness", "around": tool_only_uuid},
    )
    assert r.status_code == 404
    r = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "all", "around": tool_only_uuid},
    )
    assert r.status_code == 200


# --- Transcript messages -- view= three-way authorship filtering (Task 4, spec §5) --------
#
# `seeded_transcript` seeds one message per authorship case in the §5 semantics: a typed-human
# record, a plain Claude reply, a tool_result record, a Skill-injection record (whose Skill
# tool_use is only defined by the LATER dispatching assistant record -- the same out-of-order
# production shape `test_authorship_apply.py::_build_authorship_tree` exercises), that
# dispatching assistant record itself, an interrupt marker, and one row whose `authorship_kind`
# is reset to NULL after classification to simulate a pre-reparse row (spec §4's migrate→reparse
# deploy window). `_capture` alone never classifies (see the section above); `classify_pending`
# is called explicitly here so every OTHER row gets a real `authorship_kind`.

_VIEW_SEED_SESSION_UUID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
_VIEW_SEED_BASH_TOOL_USE_ID = "toolu_viewseed_bash0001"
_VIEW_SEED_SKILL_TOOL_USE_ID = "toolu_viewseed_skill0001"


def _build_view_seed_tree(db: Session, tmp_path: Path) -> int:
    """Capture the §5 authorship exemplar tree into a fresh main transcript and backfill
    authorship via ``classify_pending``, then reset ``u-nullkind`` back to NULL to simulate
    the pre-reparse deploy window. Returns the transcript id."""
    root = tmp_path / "view_seed_tree"
    proj = root / "-Users-x-viewseed"
    proj.mkdir(parents=True)
    lines = [
        make_user_line(
            text="please continue with the plan",
            promptSource="typed",
            origin={"kind": "human"},
            uuid="u-human",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        make_assistant_line(
            text="synthetic claude reply",
            uuid="u-claude",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        # Out-of-order production case: this tool_result's tool_use (below, in the dispatching
        # assistant record) has not been seen yet at this point in file order.
        make_tool_result_user_line(
            tool_use_id=_VIEW_SEED_BASH_TOOL_USE_ID,
            uuid="u-toolresult",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        make_user_line(
            content=[{"type": "text", "text": "Base directory for this skill: ..."}],
            isMeta=True,
            sourceToolUseID=_VIEW_SEED_SKILL_TOOL_USE_ID,
            uuid="u-skill",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        make_assistant_line(
            with_tool_use=True,
            tool_use_id=_VIEW_SEED_BASH_TOOL_USE_ID,
            extra_blocks=[
                {
                    "type": "tool_use",
                    "id": _VIEW_SEED_SKILL_TOOL_USE_ID,
                    "name": "Skill",
                    "input": {"skill": "superpowers:brainstorming"},
                }
            ],
            uuid="u-dispatcher",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        make_user_line(
            text="[Request interrupted by user]",
            uuid="u-interrupt",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
        make_user_line(
            text="pre-reparse simulated row",
            promptSource="typed",
            origin={"kind": "human"},
            uuid="u-nullkind",
            sessionId=_VIEW_SEED_SESSION_UUID,
        ),
    ]
    (proj / f"{_VIEW_SEED_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))
    _capture(db, root)

    tid = _main_transcript_id(db, _VIEW_SEED_SESSION_UUID)
    classify_pending(db)

    # Simulate the pre-reparse NULL window (spec §4/§5): a real un-backfilled row never had an
    # authorship_kind to begin with, so reset this one back to NULL after classifying it.
    null_row = db.query(Message).filter(Message.record_uuid == "u-nullkind").one()
    null_row.authorship_kind = None
    null_row.authorship_basis = None
    null_row.authorship_detail = None
    db.commit()
    return tid


@pytest.fixture
def seeded_transcript(db_session: Session, tmp_path: Path) -> int:
    return _build_view_seed_tree(db_session, tmp_path)


def _message_uuids(resp) -> list[str]:
    return [m["record_uuid"] for m in resp.json()["items"]]


def test_view_chat_shows_dialogue_and_doors(client: TestClient, seeded_transcript: int) -> None:
    # seeded: human_typed + claude-with-text + tool_result record + skill_injection
    #         + interrupt_marker + NULL-kind row (pre-reparse simulation)
    chat = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=chat")
    assert "u-human" in _message_uuids(chat) and "u-interrupt" in _message_uuids(chat)
    assert "u-toolresult" not in _message_uuids(chat) and "u-skill" not in _message_uuids(chat)
    assert "u-nullkind" in _message_uuids(chat)  # NULL falls back to legacy type+content rule

    harness = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=chat-harness")
    assert "u-skill" in _message_uuids(harness) and "u-toolresult" not in _message_uuids(harness)

    everything = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=all")
    assert "u-toolresult" in _message_uuids(everything)


def test_chat_only_param_is_gone(client: TestClient, seeded_transcript: int) -> None:
    r = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?chat_only=true&view=all")
    assert _message_uuids(r)  # unknown params ignored, view rules


def test_view_rejects_unknown_value(client: TestClient, seeded_transcript: int) -> None:
    assert (
        client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=bogus").status_code
        == 422
    )
