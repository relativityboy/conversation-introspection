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
from introspect.models import ChatSession, Favorite, Message, Transcript
from tests.conftest import (
    AGENT_HEX_ID,
    AGENT_TYPE,
    PROJECT_SLUG_1,
    PROJECT_SLUG_2,
    SESSION_UUID_1,
    SESSION_UUID_2,
    SESSION_UUID_3,
)
from tests.fixtures.records import make_assistant_line, make_session_file, make_user_line


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


def test_sessions_title_filter_matches_ai_and_custom_case_insensitive(
    db_session: Session, client: TestClient
) -> None:
    # Session 1 already carries ai_title "Synthetic Session Title"; give session 2 a matching
    # custom_title so one case-insensitive substring must hit BOTH columns.
    db_session.query(ChatSession).filter(ChatSession.session_uuid == SESSION_UUID_2).update(
        {ChatSession.custom_title: "synthetic custom marker"}
    )
    db_session.commit()

    body = client.get("/api/v1/sessions", params={"title": "SYNTHETIC"}).json()
    assert set(_uuids(body["items"])) == {SESSION_UUID_1, SESSION_UUID_2}
    assert body["total"] == 2
    # Session 3 (no matching title) is excluded.
    assert SESSION_UUID_3 not in _uuids(body["items"])


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


def test_sessions_project_filter(client: TestClient) -> None:
    only_proj2 = client.get("/api/v1/sessions", params={"project": PROJECT_SLUG_2}).json()
    assert _uuids(only_proj2["items"]) == [SESSION_UUID_3]
    assert only_proj2["total"] == 1

    proj1 = client.get("/api/v1/sessions", params={"project": PROJECT_SLUG_1}).json()
    assert set(_uuids(proj1["items"])) == {SESSION_UUID_1, SESSION_UUID_2}


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
