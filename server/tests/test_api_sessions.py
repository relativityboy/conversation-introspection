"""Read endpoints (Task P2-5): projects, sessions list/detail, transcript messages.

These exercise the real query layer against a populated archive. The app under test is
pointed at the SAME SQLite file the ``db_session`` fixture writes to (``tmp_path/archive.db``),
so a test can stage rows through ``db_session`` (capture the pinned tree, insert a Favorite,
adjust a timestamp), commit, and then read them back over HTTP -- the route handlers open
their own request-scoped sessions via ``get_db`` and see the committed data (WAL cross-conn).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.api.routes.sessions import CATEGORY_SLUGS, _categorize
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
    # Task T18: this fixture's rows are never `classify_pending`'d (NULL authorship_kind), which
    # floors to `harness-system` -- outside the new `select=`-absent default (the chat-equivalent
    # set). `select=<all five>` keeps this test's ORIGINAL, filter-independent intent (pure
    # paging/total mechanics) unaffected by that default.
    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    body = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"offset": 1, "limit": 1, "select": ",".join(CATEGORY_SLUGS)},
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
    # Task T18: see the sibling test above for why `select=<all five>` is needed here.
    body = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": ",".join(CATEGORY_SLUGS)}
    ).json()
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

    # Task T18: this tree's rows are never `classify_pending`'d, so they'd floor to
    # `harness-system` under the new `select=`-absent default -- `select=<all five>` keeps
    # this test's pure around/offset math unaffected by that default (unrelated to filtering).
    all_five = ",".join(CATEGORY_SLUGS)

    # Mid-file target: ordinal 6, limit 4 -> offset = max(0, 6 - 2) = 4, target in the page.
    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"around": ordered[6], "limit": 4, "select": all_five},
    ).json()
    assert mid["offset"] == 4
    assert ordered[6] in [m["record_uuid"] for m in mid["items"]]

    # Early target: ordinal 0 -> offset clamped to 0, target still in the page.
    early = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"around": ordered[0], "limit": 4, "select": all_five},
    ).json()
    assert early["offset"] == 0
    assert ordered[0] in [m["record_uuid"] for m in early["items"]]


def _long_tree(db_session: Session, tmp_path: Path) -> tuple[int, list[str]]:
    """A 12-message single-transcript session + its ordered record uuids (anchor tests)."""
    root = tmp_path / "anchor_tree"
    session_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    proj = root / "-Users-x-anchor"
    proj.mkdir(parents=True)
    lines = [
        (make_user_line if i % 2 == 0 else make_assistant_line)(
            text=f"anchored message {i}", sessionId=session_uuid
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
    return tid, ordered


def test_from_starts_at_anchor(db_session: Session, tmp_path: Path) -> None:
    # "entry X + N": the page begins AT the anchor and runs forward -- no index needed.
    # Task T18: `select=<all five>` keeps this test's pure paging math unaffected by the new
    # `select=`-absent default (this tree's rows are never `classify_pending`'d).
    tid, ordered = _long_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    all_five = ",".join(CATEGORY_SLUGS)

    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"from": ordered[6], "limit": 4, "select": all_five},
    ).json()
    assert mid["offset"] == 6
    assert [m["record_uuid"] for m in mid["items"]] == ordered[6:10]

    # Near the end the page truncates naturally; "entry X +" is this plus paging.
    tail = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"from": ordered[10], "limit": 4, "select": all_five},
    ).json()
    assert [m["record_uuid"] for m in tail["items"]] == ordered[10:]


def test_until_ends_at_anchor_and_never_passes_it(
    db_session: Session, tmp_path: Path
) -> None:
    # Task T18: `select=<all five>` keeps this test's pure paging math unaffected by the new
    # `select=`-absent default (this tree's rows are never `classify_pending`'d).
    tid, ordered = _long_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    all_five = ",".join(CATEGORY_SLUGS)

    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"until": ordered[6], "limit": 4, "select": all_five},
    ).json()
    assert mid["offset"] == 3
    assert [m["record_uuid"] for m in mid["items"]] == ordered[3:7]

    # Early anchor: the window clamps to the start AND truncates AT the anchor -- rows after
    # it are exactly what "until" promises not to show (unlike around's centered clamp).
    early = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"until": ordered[1], "limit": 4, "select": all_five},
    ).json()
    assert early["offset"] == 0
    assert [m["record_uuid"] for m in early["items"]] == ordered[0:2]


def test_from_and_until_unknown_anchor_are_404_problems(
    db_session: Session, client: TestClient
) -> None:
    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    for param in ("from", "until"):
        resp = client.get(
            f"/api/v1/transcripts/{tid}/messages", params={param: "no-such-record-uuid"}
        )
        assert resp.status_code == 404
        body = resp.json()
        assert set(body) == {"status", "title", "detail"}


def test_anchor_params_are_mutually_exclusive_422(
    db_session: Session, tmp_path: Path
) -> None:
    tid, ordered = _long_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    for combo in (
        {"around": ordered[3], "from": ordered[3]},
        {"from": ordered[3], "until": ordered[5]},
        {"around": ordered[3], "until": ordered[5]},
    ):
        resp = client.get(f"/api/v1/transcripts/{tid}/messages", params=combo)
        assert resp.status_code == 422
        body = resp.json()
        assert set(body) == {"status", "title", "detail"}


def test_select_chat_harness_set_from_counts_over_the_full_set(
    db_session: Session, tmp_path: Path
) -> None:
    """Every row in this tree carries a NULL `authorship_kind` (never classified). Task T18
    amendment (owner ruling 2026-09-25): a blockless `system` row now ALSO floors to
    `harness-system` at the message level, so under the chat-harness-equivalent select set,
    ALL 14 rows are visible (not just the 9 non-`system` ones) -- `from=` resolves/counts
    against the FULL set, raw id order == ordinal order."""
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    select_harness = "you-chat,claude-chat,claude-thinking,harness-system"

    target = record_uuids[6]
    assert types[6] == "attachment"

    page = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "from": target, "limit": 3},
    ).json()
    assert page["offset"] == 6
    assert [m["record_uuid"] for m in page["items"]] == record_uuids[6:9]

    # The amendment's headline case: `from=` a blockless system row now resolves (200), where
    # it used to 404 -- ordinal 0, the first row in raw order.
    system_target = record_uuids[0]
    assert types[0] == "system"
    resolved = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "from": system_target},
    )
    assert resolved.status_code == 200
    assert resolved.json()["offset"] == 0

    # Still 404 under the pure "chat" set (no harness-system) -- the amendment's new clause
    # only fires when harness-system is selected; it never removes the existing exclusion.
    still_hidden = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking", "from": system_target},
    )
    assert still_hidden.status_code == 404


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


# --- Transcript messages -- select=<chat-harness-equivalent set> type filtering ------------
#
# Originally written against the now-deleted `view=chat-harness` (Task 4, authorship spec §5);
# converted to `select=` (Task T18 -- `view=` is gone, dead code deleted per this repo's
# zero-legacy policy). None of these rows are ever authorship-classified (this tree is captured
# via `_capture`, which never calls `classify_pending`), so every row's `authorship_kind` stays
# NULL and `_categorize` floors every non-blockless row to `harness-system`.
#
# Task T18 AMENDMENT (owner ruling 2026-09-25, corrects this section's original T18 framing): a
# message with ZERO content blocks ALSO floors to `harness-system` at the MESSAGE level now (no
# block to carry a category, so the row itself does). Since this tree's `system` rows are
# blockless, `select=you-chat,claude-chat,claude-thinking,harness-system` shows ALL 14 rows, not
# just the 9 non-`system` ones -- restoring the retired `view=all`'s completeness for this tree,
# not `view=chat-harness`'s old type-based exclusion (which this section originally, and
# INCORRECTLY post-amendment, claimed to reproduce). The pure "chat" set (no `harness-system`)
# still excludes every row here (system rows via blocklessness, the rest via their NULL-kind
# `harness-system` floor not being selected) -- the amendment only ADDS a visibility path.

_VIEW_HARNESS_SESSION_UUID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

# 14 records: 5 system (blockless; hidden under the plain "chat" set, visible once
# `harness-system` is selected -- Task T18 amendment), 9 with real content (kept whenever
# `harness-system` is selected: 4 user, 4 assistant, 1 attachment). Deliberately NOT a
# truncation of the raw order -- rows land at non-contiguous raw indices so paging/
# around-centering are exercised against a real re-indexing, not just "drop a prefix/suffix".
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


def test_select_chat_harness_set_includes_blockless_system_rows_totals_and_paging(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18 amendment (owner ruling 2026-09-25): the chat-harness-equivalent select set now
    shows ALL 14 rows in this tree -- the 9 with real content (NULL authorship_kind floors to
    `harness-system`) AND the 5 blockless `system` rows (now ALSO floor to `harness-system` at
    the message level). Totals/paging run over the FULL set."""
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    assert len(record_uuids) == 14
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    select_harness = "you-chat,claude-chat,claude-thinking,harness-system"

    full = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "limit": 100},
    ).json()
    assert full["total"] == 14
    assert [m["record_uuid"] for m in full["items"]] == record_uuids
    assert any(m["type"] == "system" for m in full["items"])  # the amendment's headline case
    assert any(m["type"] == "attachment" for m in full["items"])

    page = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "offset": 2, "limit": 3},
    ).json()
    assert page["total"] == 14
    assert page["offset"] == 2
    assert [m["record_uuid"] for m in page["items"]] == record_uuids[2:5]

    # The pure "chat" set (no harness-system) still excludes EVERYTHING in this NULL-kind tree
    # -- neither visibility path (a selected block, or the new blockless-message clause) fires
    # without harness-system selected.
    chat_only = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking"},
    ).json()
    assert chat_only["total"] == 0


def test_select_chat_harness_set_around_centers_within_the_full_set(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18 amendment: around-centering runs over the FULL 14-row set under the
    chat-harness-equivalent select (blockless system rows included)."""
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    select_harness = "you-chat,claude-chat,claude-thinking,harness-system"

    # Mid target: the lone attachment row, raw/ordinal index 6 -- every row is visible now, so
    # raw id order equals ordinal order.
    target = record_uuids[6]
    assert types[6] == "attachment"

    mid = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "around": target, "limit": 4},
    ).json()
    assert mid["total"] == 14
    assert mid["offset"] == 4  # max(0, 6 - 4 // 2)
    assert [m["record_uuid"] for m in mid["items"]] == record_uuids[4:8]
    assert target in [m["record_uuid"] for m in mid["items"]]

    # Early target: a blockless SYSTEM row, ordinal 0 -- a valid anchor at all now (the
    # amendment's whole point) -- offset clamps to 0.
    early_target = record_uuids[0]
    assert types[0] == "system"
    early = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": select_harness, "around": early_target, "limit": 4},
    ).json()
    assert early["offset"] == 0
    assert early_target in [m["record_uuid"] for m in early["items"]]


def test_select_chat_harness_set_around_system_target_now_succeeds(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18 amendment (owner ruling 2026-09-25) flips this test's own former pinning: a
    blockless `system` row CAN be an anchor target now, once `harness-system` is selected -- it
    categorizes `harness-system` at the message level (no blocks to carry a category). Under the
    pure "chat" set (harness-system NOT selected), it's still unreachable -- the amendment only
    adds visibility, never removes the existing chat-set exclusion."""
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    system_target = record_uuids[0]
    assert types[0] == "system"
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={
            "select": "you-chat,claude-chat,claude-thinking,harness-system",
            "around": system_target,
        },
    )
    assert resp.status_code == 200
    assert system_target in [m["record_uuid"] for m in resp.json()["items"]]

    still_hidden = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking", "around": system_target},
    )
    assert still_hidden.status_code == 404


# NOTE(claude, Task T18): the old `view=chat-harness` content-emptiness trim section (a
# dedicated tree of tool-only/thinking-only/empty-text/unknown-kind/image-only rows) lived here.
# It exercised `_prose_visible()`/`_KNOWN_BLOCK_KINDS`, both deleted with `view=` itself. Its
# fixture combined a synthetic `tool_result`-KIND block on a NULL-authorship (never
# `classify_pending`'d) assistant MESSAGE -- a shape `_view_filter`'s message-type rule and
# `_block_matches_categories`'s block-kind rule do NOT treat identically (the block-level rule
# floors an unclassified `tool_result` block to `harness-system`, not `tool-traffic`), so it
# could not be ported to `select=` as a like-for-like conversion without asserting a DIFFERENT
# claim than the original test made. The underlying content-emptiness guard is still covered for
# `select=` by `test_select_claude_thinking_alone_returns_only_thinking_blocks` and
# `test_select_tool_traffic_returns_the_tool_exchange` (both prove an empty companion `text`
# block is pruned/never a selected block); the "deep-link into an excluded row 404s" mechanic is
# covered for `select=` by the "still hidden under the chat set" assertions in
# `test_select_chat_harness_set_from_counts_over_the_full_set`/`..._around_system_target_now_
# succeeds` above and the excluded-anchor assertion in `test_select_you_chat_claude_chat_
# claude_thinking_pins_the_old_chat_view` below (NOTE: this excluded row is one with REAL
# content in the wrong category, not a blockless one -- a blockless row is now reachable once
# `harness-system` is selected, Task T18 amendment 2026-09-25, see the blockless-rows tests
# above). Deleted rather than converted (zero-legacy policy) -- see the T18 write-up.


# --- Transcript messages -- select=<various> authorship filtering -------------------------
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


def test_select_chat_chat_harness_all_five_show_dialogue_and_doors(
    client: TestClient, seeded_transcript: int
) -> None:
    """Converted from the retired `view=chat`/`chat-harness`/`all` pins (Task T18). One
    divergence from the old `view=chat` behavior is deliberate and pinned here: `u-nullkind`
    (a human-shaped row whose `authorship_kind` was reset to NULL, simulating the pre-reparse
    window) no longer shows under the chat set -- `_categorize(None, ...)` floors unconditionally
    to `harness-system` (no legacy type-based fallback, unlike the retired `_view_filter`'s
    `legacy_fallback`), so it only reappears once `harness-system` is selected too.
    """
    # seeded: human_typed + claude-with-text + tool_result record + skill_injection
    #         + a dispatching assistant record (real narration text alongside its tool_use
    #         blocks -- NOT a content-free dispatch row) + interrupt_marker
    #         + NULL-kind row (pre-reparse simulation)
    chat = client.get(
        f"/api/v1/transcripts/{seeded_transcript}/messages"
        "?select=you-chat,claude-chat,claude-thinking"
    )
    assert "u-human" in _message_uuids(chat) and "u-interrupt" in _message_uuids(chat)
    assert "u-toolresult" not in _message_uuids(chat) and "u-skill" not in _message_uuids(chat)
    # u-dispatcher carries a real (non-empty) narration text block alongside its two tool_use
    # blocks -- that text block is claude-chat, so the ROW shows (pruned to just that block).
    assert "u-dispatcher" in _message_uuids(chat)
    dispatcher_item = _item_by_uuid(chat, "u-dispatcher")
    assert _block_kinds(dispatcher_item) == ["text"]  # its tool_use blocks are pruned out
    assert "u-nullkind" not in _message_uuids(chat)  # NULL floors to harness-system, no fallback

    harness = client.get(
        f"/api/v1/transcripts/{seeded_transcript}/messages"
        "?select=you-chat,claude-chat,claude-thinking,harness-system"
    )
    assert "u-skill" in _message_uuids(harness) and "u-toolresult" not in _message_uuids(harness)
    assert "u-nullkind" in _message_uuids(harness)  # harness-system catches the NULL floor

    everything = client.get(
        f"/api/v1/transcripts/{seeded_transcript}/messages?select={','.join(CATEGORY_SLUGS)}"
    )
    assert "u-toolresult" in _message_uuids(everything)
    # Only under all-five does u-dispatcher's tool_use pair (tool-traffic) also render.
    assert _block_kinds(_item_by_uuid(everything, "u-dispatcher")) == ["text", "tool_use", "tool_use"]


def test_view_and_unknown_params_are_silently_ignored(
    client: TestClient, seeded_transcript: int
) -> None:
    """Task T18: `view=` is deleted from `list_messages`'s signature -- any value given for it
    (bogus or not), like any other undeclared query param (`chat_only=true`, a pre-T9 relic), is
    silently dropped by FastAPI. No 422, no effect on filtering -- the response is identical to
    the bare, parameterless request (which now applies the `select=` default)."""
    bare = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages")
    with_junk = client.get(
        f"/api/v1/transcripts/{seeded_transcript}/messages"
        "?chat_only=true&view=all&view=bogus"
    )
    assert with_junk.status_code == bare.status_code == 200
    assert _message_uuids(with_junk) == _message_uuids(bare)
    assert _message_uuids(bare)  # sanity: the default select set isn't vacuous on this fixture


# --- Transcript messages -- resolved dispatch rows visible despite no prose (final review C1) --
#
# Production shape: an assistant transcript record's dispatch of a subagent carries ONE content
# block -- the ``tool_use`` itself, no accompanying text (445 production rows, 0 with any prose).
# `_prose_visible()` alone hides such a ROW in `chat`/`chat-harness`, stranding the SubagentChip
# -- the reader's sole doorway into that subagent transcript -- outside `all`, contradicting spec
# §6/§10.7(c). `_has_resolved_dispatch()` admits the row when its `tool_use` block resolves to a
# CAPTURED child transcript (``Transcript.parent_tool_use_id == tool_use_id``); an ordinary
# tool_use-only row that resolves to NOTHING must keep trimming exactly as before this fix.

_VIEW_DISPATCH_SESSION_UUID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
_VIEW_DISPATCH_RESOLVED_TOOL_USE_ID = "toolu_viewdispatch_resolved"
_VIEW_DISPATCH_UNRESOLVED_TOOL_USE_ID = "toolu_viewdispatch_unresolved"
_VIEW_DISPATCH_AGENT_HEX_ID = "dead1234"  # valid hex only -- discovery's agent-<hex>.jsonl regex


def _build_view_dispatch_tree(db: Session, tmp_path: Path) -> tuple[int, str, str]:
    """Capture a main transcript with two content-less dispatch-shaped assistant rows -- one
    ``tool_use`` resolves to a REALLY captured subagent transcript (own .jsonl + meta.json,
    same join `test_subagent_transcript_info_join_key_matches_dispatching_block` exercises),
    the other's ``tool_use_id`` matches no transcript at all -- then classifies authorship so
    both carry the real (non-NULL) `claude` kind an assistant record always gets. Returns
    ``(transcript_id, resolved_dispatch_uuid, unresolved_dispatch_uuid)``.
    """
    root = tmp_path / "view_dispatch_tree"
    proj = root / "-Users-x-viewdispatch"
    proj.mkdir(parents=True)
    # `text=""` (empty text block) + `with_tool_use=True` is this file's established idiom for a
    # "no visible prose, tool stuff only" row -- an empty text block is never counted as content
    # by `_block_matches_categories`'s `is_nonempty_or_not_text` guard (sessions.py), so the
    # row's only SELECTABLE content is the tool_use, exactly like the production shape this test
    # targets.
    lines = [
        make_assistant_line(
            text="",
            with_tool_use=True,
            tool_use_id=_VIEW_DISPATCH_RESOLVED_TOOL_USE_ID,
            uuid="u-resolved-dispatch",
            sessionId=_VIEW_DISPATCH_SESSION_UUID,
        ),
        make_assistant_line(
            text="",
            with_tool_use=True,
            tool_use_id=_VIEW_DISPATCH_UNRESOLVED_TOOL_USE_ID,
            uuid="u-unresolved-dispatch",
            sessionId=_VIEW_DISPATCH_SESSION_UUID,
        ),
    ]
    (proj / f"{_VIEW_DISPATCH_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))

    subagents_dir = proj / _VIEW_DISPATCH_SESSION_UUID / "subagents"
    subagents_dir.mkdir(parents=True)
    subagent_lines = [
        make_user_line(
            text="synthetic subagent prompt", sessionId=_VIEW_DISPATCH_SESSION_UUID
        ),
        make_assistant_line(
            text="synthetic subagent reply", sessionId=_VIEW_DISPATCH_SESSION_UUID
        ),
    ]
    (subagents_dir / f"agent-{_VIEW_DISPATCH_AGENT_HEX_ID}.jsonl").write_bytes(
        make_session_file(subagent_lines)
    )
    (subagents_dir / f"agent-{_VIEW_DISPATCH_AGENT_HEX_ID}.meta.json").write_text(
        json.dumps(
            {
                "agentType": "Explore",
                "description": "Synthetic dispatch-resolution fixture agent.",
                "toolUseId": _VIEW_DISPATCH_RESOLVED_TOOL_USE_ID,
            }
        )
    )

    _capture(db, root)
    classify_pending(db)
    db.commit()

    tid = _main_transcript_id(db, _VIEW_DISPATCH_SESSION_UUID)
    return tid, "u-resolved-dispatch", "u-unresolved-dispatch"


# NOTE(claude, Task T18): `test_view_chat_and_harness_show_resolved_dispatch_rows_with_no_prose`
# lived here (proving `view=chat`/`chat-harness`/`all` each showed a resolved-dispatch row and
# excluded an unresolved one). Deleted as redundant, not converted: the SAME claim, for the sole
# remaining mechanism, is proven by `test_select_chat_preset_pins_the_old_chat_view_including_
# resolved_dispatch_chip` below (`select=`'s "chat" 3-slug set) -- and `_select_filter` is a
# monotonic union over selected categories (adding `harness-system` to reach the chat-harness
# set can only ADD visible rows, never remove one), so a dedicated chat-harness-set repeat would
# prove nothing the chat-set pin doesn't already imply structurally.


def test_select_chat_around_resolved_dispatch_row_succeeds(
    db_session: Session, tmp_path: Path
) -> None:
    """The 404-avoidance half of the same fix, converted to `select=` (Task T18): deep-linking
    `around=` a resolved dispatch row under the chat-equivalent select set must resolve it as a
    real ordinal, not 404 as if it were trimmed."""
    tid, resolved_uuid, _unresolved_uuid = _build_view_dispatch_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    r = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking", "around": resolved_uuid},
    )
    assert r.status_code == 200
    assert resolved_uuid in _message_uuids(r)


# --- Transcript messages -- select= category filtering (Task T9) -------------------------
#
# Generalizes the fixed `view=` presets to arbitrary category selection. Every (message,
# block) maps to EXACTLY ONE of five categories (`_categorize`, sessions.py): you-chat,
# claude-chat, claude-thinking, tool-traffic, harness-system.
#
# `_categorize`'s exhaustiveness is proven directly (pure function, no HTTP) below. The
# endpoint-level equivalence tests use a "clean" fixture where every message's blocks belong to
# a SINGLE category group (no message mixes e.g. a `tool_use` block with narration text) --
# `view=` never filters blocks WITHIN an already-visible row (only `select=` does), so a row
# that straddles categories is only ROW-equivalent, not BLOCK-equivalent, between the two
# mechanisms; keeping the equivalence fixture single-category-per-message sidesteps that
# without hiding it -- the straddling cases (a claude turn combining `thinking` + `text`, a
# resolved dispatch chip, a blockless `system` row) are each exercised by their own dedicated
# test below instead, and are written up in
# claude_notes/2026-09-22-sdd-checkboxes-writeups.md.


# All 20 authorship kinds `schema/authorship.py::classify` can produce (read off every
# `Authorship("...", ...)` literal in that module) -- this list is the exhaustiveness oracle,
# not a subset.
_ALL_AUTHORSHIP_KINDS = [
    "compact_summary", "tool_result", "skill_injection", "tool_injection", "task_notification",
    "coordinator", "human_typed", "human_queued", "sdk_automation", "command_expansion",
    "command_output", "harness_meta", "interrupt_marker", "dispatch", "unclassified",
    "human_inferred", "claude", "system", "attachment_queued_human", "attachment_furniture",
]
_ALL_BLOCK_KINDS = ["text", "thinking", "tool_use", "tool_result", "image", "document", "fallback"]


def test_categorize_is_exhaustive_and_total() -> None:
    """`_categorize` must return one of the five frozen slugs for every (authorship_kind,
    block_kind) pair, including None authorship (the pre-reparse NULL window) and a made-up
    forward-drift block kind -- never raise, never return something outside `CATEGORY_SLUGS`."""
    for authorship_kind in [*_ALL_AUTHORSHIP_KINDS, None, "some-future-unclassified-kind"]:
        for block_kind in [*_ALL_BLOCK_KINDS, "some-future-block-kind"]:
            assert _categorize(authorship_kind, block_kind) in CATEGORY_SLUGS


def test_categorize_tool_result_message_wins_regardless_of_block_kind() -> None:
    # Spec: "AND everything of tool_result-kind messages" -- the message-level override beats
    # whatever the block itself looks like.
    for block_kind in _ALL_BLOCK_KINDS:
        assert _categorize("tool_result", block_kind) == "tool-traffic"


def test_categorize_tool_use_block_wins_over_family() -> None:
    # Spec: "tool_use blocks (wherever they appear)" -- even on a you-chat/harness kind (never
    # happens in practice, since only AssistantRecord ever emits tool_use, but the priority
    # must still hold structurally). No `tool_use_id`/resolved-set given -> always unresolved.
    for authorship_kind in ("human_typed", "harness_meta", "claude", "dispatch", "coordinator"):
        assert _categorize(authorship_kind, "tool_use") == "tool-traffic"


def test_categorize_resolved_dispatch_tool_use_is_claude_chat() -> None:
    """Owner ruling 2026-09-23 (Task T12): a resolved-dispatch `tool_use` block -- its
    `tool_use_id` present in the caller-supplied resolved-dispatch set -- is `claude-chat`, a
    doorway into a Claude-voiced conversation, not mechanical traffic. An unresolved `tool_use`
    (id absent from the set, or no id at all) stays `tool-traffic`, unconditionally, even on a
    you-chat/harness authorship kind (structurally unreachable today, but the priority holds)."""
    resolved = frozenset({"toolu_resolved"})
    for authorship_kind in ("claude", "dispatch", "coordinator", "human_typed", "harness_meta"):
        assert (
            _categorize(authorship_kind, "tool_use", "toolu_resolved", resolved) == "claude-chat"
        )
        assert _categorize(authorship_kind, "tool_use", "toolu_other", resolved) == "tool-traffic"
        assert _categorize(authorship_kind, "tool_use", None, resolved) == "tool-traffic"
    # A `tool_result`-authorship message still wins over even a resolved id (priority 1 first).
    assert _categorize("tool_result", "tool_use", "toolu_resolved", resolved) == "tool-traffic"


def test_categorize_you_chat_kinds() -> None:
    for kind in (
        "human_typed", "human_queued", "human_inferred", "attachment_queued_human",
        "interrupt_marker",
    ):
        assert _categorize(kind, "text") == "you-chat"


def test_categorize_claude_family_splits_thinking_from_everything_else() -> None:
    for kind in ("claude", "dispatch", "coordinator"):
        assert _categorize(kind, "thinking") == "claude-thinking"
        for block_kind in ("text", "image", "document", "fallback"):
            assert _categorize(kind, block_kind) == "claude-chat"


def test_categorize_harness_system_is_the_exhaustive_floor() -> None:
    harness_kinds = (
        "compact_summary", "skill_injection", "tool_injection", "task_notification",
        "sdk_automation", "command_expansion", "command_output", "harness_meta",
        "unclassified", "system", "attachment_furniture", None, "some-future-kind",
    )
    for kind in harness_kinds:
        assert _categorize(kind, "text") == "harness-system"
    # A `thinking` block outside the claude family -- structurally unreachable today (only
    # AssistantRecord emits thinking, and AssistantRecord always classifies "claude"), but the
    # contract calls it out explicitly ("thinking outside claude-family if that exists") as a
    # forward-tolerance case, so it is proven here rather than left implicit.
    assert _categorize("harness_meta", "thinking") == "harness-system"


# --- select= equivalence fixture -----------------------------------------------------------

_SELECT_SESSION_UUID = "12121212-1212-4212-8212-121212121212"


def _build_select_equivalence_tree(db: Session, tmp_path: Path) -> tuple[int, dict[str, str]]:
    """One message per category, each with blocks entirely inside a SINGLE category (see the
    section docstring above). Returns ``(transcript_id, {label: record_uuid})``."""
    root = tmp_path / "select_tree"
    proj = root / "-Users-x-select"
    proj.mkdir(parents=True)
    lines = [
        make_user_line(
            text="you-chat: a synthetic human message",
            promptSource="typed",
            origin={"kind": "human"},
            uuid="u-you-chat",
            sessionId=_SELECT_SESSION_UUID,
        ),
        make_assistant_line(
            text="claude-chat: a synthetic claude reply, no thinking, no tool use",
            uuid="u-claude-chat",
            sessionId=_SELECT_SESSION_UUID,
        ),
        make_assistant_line(
            text="claude-chat half of a thinking+text turn",
            with_thinking=True,
            thinking_text="claude-thinking half of a thinking+text turn",
            uuid="u-claude-thinking",
            sessionId=_SELECT_SESSION_UUID,
        ),
        make_tool_result_user_line(
            tool_use_id="toolu_orphan_select_test",
            result_text="tool-traffic: a synthetic tool result",
            uuid="u-tool-result",
            sessionId=_SELECT_SESSION_UUID,
        ),
        make_user_line(
            content=[
                {
                    "type": "text",
                    "text": "<system-reminder>harness-system: synthetic harness note</system-reminder>",
                }
            ],
            isMeta=True,
            uuid="u-harness",
            sessionId=_SELECT_SESSION_UUID,
        ),
        make_user_line(
            text="[Request interrupted by user]",
            uuid="u-interrupt",
            sessionId=_SELECT_SESSION_UUID,
        ),
    ]
    (proj / f"{_SELECT_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))
    _capture(db, root)
    classify_pending(db)
    db.commit()

    tid = _main_transcript_id(db, _SELECT_SESSION_UUID)
    return tid, {
        "you_chat": "u-you-chat",
        "claude_chat": "u-claude-chat",
        "claude_thinking": "u-claude-thinking",
        "tool_result": "u-tool-result",
        "harness": "u-harness",
        "interrupt": "u-interrupt",
    }


def _item_by_uuid(resp, uuid: str) -> dict:
    items = {m["record_uuid"]: m for m in resp.json()["items"]}
    assert uuid in items, f"{uuid} missing from response"
    return items[uuid]


def _block_kinds(item: dict) -> list[str]:
    return [b["block_kind"] for b in item["blocks"]]


def test_select_you_chat_claude_chat_claude_thinking_pins_the_old_chat_view(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18: converted from the `view=chat` equivalence test (T9) into a direct behavior
    pin now that `view=` is deleted -- every row/block expectation below is unchanged from what
    the fixture always encoded (`_build_select_equivalence_tree`'s docstring: single-category
    messages plus one thinking+text row, so a claude turn combining `thinking` + `text` shows
    BOTH blocks -- `select=` prunes BLOCKS, so the set that keeps both is `{you-chat, claude-chat,
    claude-thinking}`, not a naive 2-slug set).

    Also pins the T18 default: an ABSENT `select=` now falls back to this exact 3-slug set (the
    "chat-equivalent" default, preserving the old default reader view for parameterless API
    consumers) -- proven identical row-for-row and block-for-block, not just same-shaped.
    """
    tid, u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    select_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking"},
    )
    default_resp = client.get(f"/api/v1/transcripts/{tid}/messages")
    assert select_resp.status_code == default_resp.status_code == 200

    expected = {u["you_chat"], u["claude_chat"], u["claude_thinking"], u["interrupt"]}
    select_uuids = set(_message_uuids(select_resp))
    default_uuids = set(_message_uuids(default_resp))
    assert select_uuids == default_uuids == expected
    assert select_resp.json()["total"] == default_resp.json()["total"] == 4

    # Same blocks too, not just the same rows -- the load-bearing part of the pin.
    for label in ("you_chat", "claude_chat", "claude_thinking", "interrupt"):
        uuid = u[label]
        select_item = _item_by_uuid(select_resp, uuid)
        default_item = _item_by_uuid(default_resp, uuid)
        assert _block_kinds(select_item) == _block_kinds(default_item)
        assert [b["text_content"] for b in select_item["blocks"]] == [
            b["text_content"] for b in default_item["blocks"]
        ]
    # The thinking+text row specifically carries BOTH block kinds.
    assert _block_kinds(_item_by_uuid(select_resp, u["claude_thinking"])) == ["thinking", "text"]

    # A row excluded from the chat set (tool_result -> tool-traffic) can't be an anchor target --
    # the excluded-anchor-404 mechanic the retired view=chat-harness paging tests used to pin,
    # now proven directly against `select=` (Task T18; see the deleted-section note above).
    anchor_404 = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking", "around": u["tool_result"]},
    )
    assert anchor_404.status_code == 404


# --- Task T12: the chat-preset<->select-set equivalence, strengthened with a resolved chip -----
#
# Owner ruling 2026-09-23 makes a resolved-dispatch `tool_use` block `claude-chat` (T9's own
# equivalence fixture above deliberately EXCLUDED a resolved-dispatch row -- see its "Discovered
# nuances" #2 in the write-up -- because under T9's rules the equivalence didn't hold for it).
# This fixture adds one resolved AND one unresolved dispatch-shaped row (same idiom as
# `_build_view_dispatch_tree`) on top of the existing single-category tree, so the equivalence
# can be proven chip-for-chip, not just for the single-category rows.

_SELECT_DISPATCH_SESSION_UUID = "56565656-5656-4656-8656-565656565656"
_SELECT_DISPATCH_RESOLVED_TOOL_USE_ID = "toolu_selectdispatch_resolved"
_SELECT_DISPATCH_UNRESOLVED_TOOL_USE_ID = "toolu_selectdispatch_unresolved"
_SELECT_DISPATCH_AGENT_HEX_ID = "beef5678"  # valid hex only -- discovery's agent-<hex>.jsonl regex


def _build_select_equivalence_tree_with_resolved_dispatch(
    db: Session, tmp_path: Path
) -> tuple[int, dict[str, str]]:
    """`_build_select_equivalence_tree`'s six single-category rows PLUS a resolved and an
    unresolved dispatch-shaped row (`_build_view_dispatch_tree`'s idiom: empty text + tool_use,
    one `tool_use_id` resolving to a REALLY captured subagent transcript, the other resolving to
    nothing). Returns ``(transcript_id, {label: record_uuid})`` with two new keys,
    ``resolved_dispatch``/``unresolved_dispatch``, alongside the original six."""
    root = tmp_path / "select_tree_with_dispatch"
    proj = root / "-Users-x-selectdispatch"
    proj.mkdir(parents=True)
    lines = [
        make_user_line(
            text="you-chat: a synthetic human message",
            promptSource="typed",
            origin={"kind": "human"},
            uuid="u-you-chat",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_assistant_line(
            text="claude-chat: a synthetic claude reply, no thinking, no tool use",
            uuid="u-claude-chat",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_assistant_line(
            text="claude-chat half of a thinking+text turn",
            with_thinking=True,
            thinking_text="claude-thinking half of a thinking+text turn",
            uuid="u-claude-thinking",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_tool_result_user_line(
            tool_use_id="toolu_orphan_select_dispatch_test",
            result_text="tool-traffic: a synthetic tool result",
            uuid="u-tool-result",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_user_line(
            content=[
                {
                    "type": "text",
                    "text": "<system-reminder>harness-system: synthetic harness note</system-reminder>",
                }
            ],
            isMeta=True,
            uuid="u-harness",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_user_line(
            text="[Request interrupted by user]",
            uuid="u-interrupt",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_assistant_line(
            text="",
            with_tool_use=True,
            tool_use_id=_SELECT_DISPATCH_RESOLVED_TOOL_USE_ID,
            uuid="u-resolved-dispatch",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
        make_assistant_line(
            text="",
            with_tool_use=True,
            tool_use_id=_SELECT_DISPATCH_UNRESOLVED_TOOL_USE_ID,
            uuid="u-unresolved-dispatch",
            sessionId=_SELECT_DISPATCH_SESSION_UUID,
        ),
    ]
    (proj / f"{_SELECT_DISPATCH_SESSION_UUID}.jsonl").write_bytes(make_session_file(lines))

    subagents_dir = proj / _SELECT_DISPATCH_SESSION_UUID / "subagents"
    subagents_dir.mkdir(parents=True)
    subagent_lines = [
        make_user_line(
            text="synthetic subagent prompt", sessionId=_SELECT_DISPATCH_SESSION_UUID
        ),
        make_assistant_line(
            text="synthetic subagent reply", sessionId=_SELECT_DISPATCH_SESSION_UUID
        ),
    ]
    (subagents_dir / f"agent-{_SELECT_DISPATCH_AGENT_HEX_ID}.jsonl").write_bytes(
        make_session_file(subagent_lines)
    )
    (subagents_dir / f"agent-{_SELECT_DISPATCH_AGENT_HEX_ID}.meta.json").write_text(
        json.dumps(
            {
                "agentType": "Explore",
                "description": "Synthetic select-equivalence dispatch fixture agent.",
                "toolUseId": _SELECT_DISPATCH_RESOLVED_TOOL_USE_ID,
            }
        )
    )

    _capture(db, root)
    classify_pending(db)
    db.commit()

    tid = _main_transcript_id(db, _SELECT_DISPATCH_SESSION_UUID)
    return tid, {
        "you_chat": "u-you-chat",
        "claude_chat": "u-claude-chat",
        "claude_thinking": "u-claude-thinking",
        "tool_result": "u-tool-result",
        "harness": "u-harness",
        "interrupt": "u-interrupt",
        "resolved_dispatch": "u-resolved-dispatch",
        "unresolved_dispatch": "u-unresolved-dispatch",
    }


def test_select_chat_preset_pins_the_old_chat_view_including_resolved_dispatch_chip(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18: converted from the `view=chat` (T12-strengthened) equivalence test into a
    direct pin. Strengthens `test_select_you_chat_claude_chat_claude_thinking_pins_the_old_chat_
    view` with a resolved dispatch row in the mix (owner ruling 2026-09-23, Task T12): the
    resolved dispatch row (a `tool_use` block, categorized `claude-chat`) shows under the chat
    set, block-identical to how `view=chat` used to render it, while the unresolved dispatch row
    (stays `tool-traffic`) is excluded."""
    tid, u = _build_select_equivalence_tree_with_resolved_dispatch(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    select_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking"},
    )
    assert select_resp.status_code == 200

    expected = {
        u["you_chat"], u["claude_chat"], u["claude_thinking"], u["interrupt"],
        u["resolved_dispatch"],
    }
    select_uuids = set(_message_uuids(select_resp))
    assert select_uuids == expected
    assert u["unresolved_dispatch"] not in select_uuids
    assert u["tool_result"] not in select_uuids

    resolved_item = _item_by_uuid(select_resp, u["resolved_dispatch"])
    assert _block_kinds(resolved_item) == ["text", "tool_use"]

    # select=<all five> is the only set that also surfaces the UNRESOLVED dispatch row (plain
    # tool-traffic) -- the chat set's selective doorway isn't a general "hide all tool_use" rule.
    all_five_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": ",".join(CATEGORY_SLUGS)}
    )
    all_five_uuids = set(_message_uuids(all_five_resp))
    assert u["resolved_dispatch"] in all_five_uuids
    assert u["unresolved_dispatch"] in all_five_uuids


def test_select_full_chat_harness_set_pins_the_old_chat_harness_view(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18: converted from the `view=chat-harness` equivalence test into a direct pin."""
    tid, u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    select_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking,harness-system"},
    )
    assert select_resp.status_code == 200

    expected = {
        u["you_chat"], u["claude_chat"], u["claude_thinking"], u["interrupt"], u["harness"],
    }
    select_uuids = set(_message_uuids(select_resp))
    assert select_uuids == expected
    # tool_result stays OUT of the chat-harness set.
    assert u["tool_result"] not in select_uuids


def test_select_all_five_pins_the_old_all_view(db_session: Session, tmp_path: Path) -> None:
    """Task T18: converted from the `view=all` equivalence test into a direct pin. This
    fixture has no blockless row, so it can't exercise the blockless-message clause (Task T18
    amendment 2026-09-25: a blockless message categorizes `harness-system` at the message
    level) one way or the other; `test_select_shows_blockless_rows_once_harness_system_is_
    selected` below is where that clause is pinned, on a fixture that DOES have blockless
    rows."""
    tid, u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    select_resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": ",".join(CATEGORY_SLUGS)},
    )
    assert select_resp.status_code == 200

    all_uuids = set(u.values())
    select_uuids = set(_message_uuids(select_resp))
    assert select_uuids == all_uuids
    assert select_resp.json()["total"] == len(all_uuids)

    # Sanity: every row's blocks are non-empty (the fixture is well-formed) -- there is nothing
    # further to prune once ALL five categories are selected.
    for uuid in all_uuids:
        assert _block_kinds(_item_by_uuid(select_resp, uuid))


def test_select_claude_thinking_alone_returns_only_thinking_blocks(
    db_session: Session, tmp_path: Path
) -> None:
    tid, u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    resp = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": "claude-thinking"}
    )
    assert resp.status_code == 200
    uuids = _message_uuids(resp)
    assert uuids == [u["claude_thinking"]]
    item = _item_by_uuid(resp, u["claude_thinking"])
    # Its `text` block (category claude-chat) is pruned -- only the thinking block renders.
    assert _block_kinds(item) == ["thinking"]


def test_select_tool_traffic_returns_the_tool_exchange(
    db_session: Session, tmp_path: Path
) -> None:
    """`tool-traffic` is one category on purpose: a `tool_use` row and its `tool_result`
    row are both admitted -- "the exchange is a unit"."""
    root = tmp_path / "select_tool_exchange_tree"
    proj = root / "-Users-x-selecttools"
    proj.mkdir(parents=True)
    session_uuid = "34343434-3434-4434-8434-343434343434"
    tool_use_id = "toolu_select_exchange"
    lines = [
        make_assistant_line(
            text="",
            with_tool_use=True,
            tool_use_id=tool_use_id,
            uuid="u-tool-use",
            sessionId=session_uuid,
        ),
        make_tool_result_user_line(
            tool_use_id=tool_use_id,
            uuid="u-tool-result-exchange",
            sessionId=session_uuid,
        ),
    ]
    (proj / f"{session_uuid}.jsonl").write_bytes(make_session_file(lines))
    _capture(db_session, root)
    classify_pending(db_session)
    db_session.commit()
    tid = _main_transcript_id(db_session, session_uuid)

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get(f"/api/v1/transcripts/{tid}/messages", params={"select": "tool-traffic"})
    assert resp.status_code == 200
    assert set(_message_uuids(resp)) == {"u-tool-use", "u-tool-result-exchange"}
    tool_use_item = _item_by_uuid(resp, "u-tool-use")
    # The empty text block (category claude-chat) is pruned; only the tool_use block remains.
    assert _block_kinds(tool_use_item) == ["tool_use"]


def test_view_param_has_no_effect_when_present_alongside_select(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18: `view=` is no longer a declared param on `list_messages` at all -- passing one
    alongside `select=` is exactly as inert as passing any other undeclared query param (FastAPI
    silently drops it). `select=tool-traffic` governs regardless of what `view=` says."""
    tid, u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    both = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"view": "chat", "select": "tool-traffic"},
    )
    assert _message_uuids(both) == [u["tool_result"]]

    # Same result with no `view=` at all -- proving `view=chat` above contributed nothing.
    select_only = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": "tool-traffic"}
    )
    assert _message_uuids(both) == _message_uuids(select_only)


def test_select_empty_is_422(db_session: Session, tmp_path: Path) -> None:
    tid, _u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get(f"/api/v1/transcripts/{tid}/messages", params={"select": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert "empty" in body["detail"]


def test_select_unknown_slug_is_422_naming_it(db_session: Session, tmp_path: Path) -> None:
    tid, _u = _build_select_equivalence_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,bogus-category"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "bogus-category" in body["detail"]
    for slug in CATEGORY_SLUGS:
        assert slug in body["detail"]


# --- select= discovered-nuance regression tests (documented in the write-up) --------------


def test_select_reproduces_resolved_dispatch_chip_visibility_as_claude_chat(
    db_session: Session, tmp_path: Path
) -> None:
    """Owner ruling 2026-09-23 (Task T12): a resolved-dispatch `tool_use` block (the
    SubagentChip's only doorway, dispatching a CAPTURED subagent transcript) is a doorway into a
    Claude-voiced conversation, not mechanical traffic, so it categorizes `claude-chat`.
    `select=you-chat,claude-chat,claude-thinking` (the `chat` preset's set -- and, per Task T18,
    the default when `select=` is absent) shows it; an ORDINARY (unresolved) `tool_use` row does
    not. The ruling's flip side: with `claude-chat` UNSELECTED and only `tool-traffic` selected,
    the resolved chip disappears (its block is no longer tool-traffic) while the unresolved row
    still shows -- tool-traffic is unaffected for the unresolved case.
    """
    tid, resolved_uuid, unresolved_uuid = _build_view_dispatch_tree(db_session, tmp_path)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    selected = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking"},
    )
    assert resolved_uuid in _message_uuids(selected)
    assert unresolved_uuid not in _message_uuids(selected)

    # An absent `select=` (Task T18 default) behaves identically.
    default = client.get(f"/api/v1/transcripts/{tid}/messages")
    assert resolved_uuid in _message_uuids(default)
    assert unresolved_uuid not in _message_uuids(default)

    # The ruling's flip side: claude-chat unselected, tool-traffic selected -- the resolved chip
    # is NOT shown (its tool_use block is claude-chat now, not tool-traffic), but the ordinary
    # unresolved tool_use row still is.
    tools_only = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": "tool-traffic"}
    )
    assert resolved_uuid not in _message_uuids(tools_only)
    assert unresolved_uuid in _message_uuids(tools_only)

    # select=<all five> shows both -- the chat set's exclusivity isn't "hide all tool_use".
    all_five = client.get(
        f"/api/v1/transcripts/{tid}/messages", params={"select": ",".join(CATEGORY_SLUGS)}
    )
    assert resolved_uuid in _message_uuids(all_five)
    assert unresolved_uuid in _message_uuids(all_five)


def test_select_shows_blockless_rows_once_harness_system_is_selected(
    db_session: Session, tmp_path: Path
) -> None:
    """Task T18 amendment (owner ruling 2026-09-25, superseding this test's own former pinning
    of "structurally unreachable"): a message with ZERO content blocks (`SystemRecord.blocks()`
    is always `[]`) categorizes as `harness-system` at the MESSAGE level -- there's no block to
    carry a category, so the row itself floors there. `select=`'s disappear rule gains a second
    clause beside the per-block EXISTS: a blockless row is visible iff `harness-system` is
    selected. This restores `view=all`'s old completeness for blockless rows specifically (not
    a general "every row unconditionally" -- a row DOES still need `harness-system` selected).

    Under the plain "chat" default/set (no `harness-system`), a blockless row is still invisible
    -- the amendment only ADDS a visibility path, it never removes the existing one."""
    tid, record_uuids, types = _build_view_harness_tree(db_session, tmp_path)
    system_uuids = {u for u, t in zip(record_uuids, types) if t == "system"}
    assert system_uuids  # sanity: the shared harness tree really does carry system rows
    non_system_uuids = set(record_uuids) - system_uuids
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    # Hidden under the default/chat set (no harness-system selected) -- unaffected by the
    # amendment.
    chat_only = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking"},
    )
    assert not (system_uuids & set(_message_uuids(chat_only)))

    default_absent = client.get(f"/api/v1/transcripts/{tid}/messages")
    assert not (system_uuids & set(_message_uuids(default_absent)))

    # Visible once harness-system is selected (chat-harness-equivalent set) -- the amendment.
    chat_harness = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": "you-chat,claude-chat,claude-thinking,harness-system"},
    )
    assert system_uuids <= set(_message_uuids(chat_harness))
    assert non_system_uuids <= set(_message_uuids(chat_harness))

    # And under all-five.
    select_all = client.get(
        f"/api/v1/transcripts/{tid}/messages",
        params={"select": ",".join(CATEGORY_SLUGS)},
    )
    assert system_uuids <= set(_message_uuids(select_all))
    assert non_system_uuids <= set(_message_uuids(select_all))
