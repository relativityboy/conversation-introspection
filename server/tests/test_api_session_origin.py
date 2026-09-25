"""Session origin (Task T13, owner ruling 2026-09-24): `root` / `subagent` / `empty`.

Distinguishes sessions a human actually typed into (`root`) from standalone dispatched runs
that never had a human turn (`subagent` -- a security review, a minion implementation) and
sessions with no messages at all (`empty`). Computed, not stored (no schema change): see
``introspect.api.routes.sessions._session_origin``.

These build small custom trees (own project dir) rather than the shared pinned
``fixture_tree`` -- precise control over which messages are human-authored is the whole
point, and the pinned fixture's ``client`` fixture in ``test_api_sessions.py`` never calls
``classify_pending`` (authorship_kind stays NULL there), which would make every origin look
like `subagent` regardless of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.interpret import classify_pending
from introspect.models import ChatSession, Message, Transcript
from tests.fixtures.records import make_assistant_line, make_session_file, make_user_line

ORIGIN_ROOT_SESSION = "10101010-1111-4111-8111-101010101010"
ORIGIN_SUBAGENT_SESSION = "20202020-1111-4111-8111-202020202020"
ORIGIN_EMPTY_SESSION = "30303030-1111-4111-8111-303030303030"

ORIGIN_PROJECT_SLUG = "-Users-x-origin"


def _capture_and_classify(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    classify_pending(db)
    db.commit()


def _build_origin_tree(tmp_path: Path) -> Path:
    root = tmp_path / "origin_tree"
    proj = root / ORIGIN_PROJECT_SLUG
    proj.mkdir(parents=True)

    root_lines = [
        make_user_line(
            text="please look into this", sessionId=ORIGIN_ROOT_SESSION, promptSource="typed"
        ),
        make_assistant_line(text="on it", sessionId=ORIGIN_ROOT_SESSION),
    ]
    # Brief's exemplar: "a session with only claude-authored messages = subagent" -- no user
    # record at all, so there is never a human turn to classify.
    subagent_lines = [
        make_assistant_line(
            text="dispatched work finished without incident", sessionId=ORIGIN_SUBAGENT_SESSION
        ),
    ]
    (proj / f"{ORIGIN_ROOT_SESSION}.jsonl").write_bytes(make_session_file(root_lines))
    (proj / f"{ORIGIN_SUBAGENT_SESSION}.jsonl").write_bytes(make_session_file(subagent_lines))
    return root


@pytest.fixture
def client(db_session: Session, tmp_path: Path) -> TestClient:
    root = _build_origin_tree(tmp_path)
    _capture_and_classify(db_session, root)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


def _origin_by_uuid(client: TestClient, **params: object) -> dict[str, str]:
    body = client.get("/api/v1/sessions", params=params).json()
    return {i["session_uuid"]: i["origin"] for i in body["items"]}


# --- Origin computation -------------------------------------------------------------------


def test_origin_root_has_human_message(client: TestClient) -> None:
    assert _origin_by_uuid(client)[ORIGIN_ROOT_SESSION] == "root"


def test_origin_subagent_no_human_message(client: TestClient) -> None:
    assert _origin_by_uuid(client)[ORIGIN_SUBAGENT_SESSION] == "subagent"


def test_origin_empty_zero_messages(db_session: Session, client: TestClient) -> None:
    # "a title-only/no-message session = empty" (brief's exemplar): a bare ChatSession row,
    # no transcript, no message -- only a title.
    db_session.add(
        ChatSession(session_uuid=ORIGIN_EMPTY_SESSION, project_id=1, custom_title="Untitled draft")
    )
    db_session.commit()
    assert _origin_by_uuid(client)[ORIGIN_EMPTY_SESSION] == "empty"


def test_origin_root_via_human_message_in_subagent_transcript_only(
    db_session: Session, client: TestClient
) -> None:
    """Origin is computed across ANY of the session's transcripts, not just main (brief item
    1): a session whose MAIN transcript carries no human message is still `root` if a human
    message somehow lives in one of its SUBAGENT transcripts. Real subagent transcripts never
    classify human (rule 13 floors sidechain/subagent-kind records to `dispatch`), so this
    reaches the state the same way ``test_search_fts5.py``'s ``_seed_sourced_block`` does:
    retag an existing captured message directly."""
    msg = (
        db_session.query(Message)
        .join(Transcript, Transcript.id == Message.transcript_id)
        .filter(Transcript.session_id == ORIGIN_SUBAGENT_SESSION)
        .order_by(Message.id)
        .first()
    )
    assert msg is not None
    msg.authorship_kind = "human_typed"
    db_session.commit()

    assert _origin_by_uuid(client)[ORIGIN_SUBAGENT_SESSION] == "root"


def test_session_detail_carries_origin(client: TestClient) -> None:
    body = client.get(f"/api/v1/sessions/{ORIGIN_ROOT_SESSION}").json()
    assert body["origin"] == "root"
    body = client.get(f"/api/v1/sessions/{ORIGIN_SUBAGENT_SESSION}").json()
    assert body["origin"] == "subagent"


# --- origin= filter -------------------------------------------------------------------------


def test_sessions_origin_filter_root(client: TestClient) -> None:
    body = client.get("/api/v1/sessions", params={"origin": "root"}).json()
    uuids = {i["session_uuid"] for i in body["items"]}
    assert ORIGIN_ROOT_SESSION in uuids
    assert ORIGIN_SUBAGENT_SESSION not in uuids


def test_sessions_origin_filter_subagent(client: TestClient) -> None:
    body = client.get("/api/v1/sessions", params={"origin": "subagent"}).json()
    uuids = {i["session_uuid"] for i in body["items"]}
    assert uuids == {ORIGIN_SUBAGENT_SESSION}


def test_sessions_origin_filter_empty(db_session: Session, client: TestClient) -> None:
    db_session.add(ChatSession(session_uuid=ORIGIN_EMPTY_SESSION, project_id=1))
    db_session.commit()
    body = client.get("/api/v1/sessions", params={"origin": "empty"}).json()
    assert {i["session_uuid"] for i in body["items"]} == {ORIGIN_EMPTY_SESSION}


def test_sessions_origin_filter_csv_unions(db_session: Session, client: TestClient) -> None:
    db_session.add(ChatSession(session_uuid=ORIGIN_EMPTY_SESSION, project_id=1))
    db_session.commit()
    body = client.get("/api/v1/sessions", params={"origin": "root,empty"}).json()
    uuids = {i["session_uuid"] for i in body["items"]}
    assert uuids == {ORIGIN_ROOT_SESSION, ORIGIN_EMPTY_SESSION}
    assert ORIGIN_SUBAGENT_SESSION not in uuids


def test_sessions_origin_absent_is_unfiltered(client: TestClient) -> None:
    body = client.get("/api/v1/sessions").json()
    uuids = {i["session_uuid"] for i in body["items"]}
    assert {ORIGIN_ROOT_SESSION, ORIGIN_SUBAGENT_SESSION} <= uuids


def test_sessions_origin_filter_unknown_slug_is_422_naming_it(client: TestClient) -> None:
    resp = client.get("/api/v1/sessions", params={"origin": "bogus"})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert "bogus" in body["detail"]
    for slug in ("root", "subagent", "empty"):
        assert slug in body["detail"]


def test_sessions_origin_filter_empty_value_is_422(client: TestClient) -> None:
    resp = client.get("/api/v1/sessions", params={"origin": ""})
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"]


# --- origin_counts ----------------------------------------------------------------------


def test_sessions_origin_counts_present_and_correct(client: TestClient) -> None:
    body = client.get("/api/v1/sessions").json()
    counts = body["origin_counts"]
    assert set(counts) == {"root", "subagent", "empty"}
    assert counts["root"] == 1
    assert counts["subagent"] == 1
    assert counts["empty"] == 0


def test_sessions_origin_counts_ignore_the_origin_filter_itself(client: TestClient) -> None:
    unfiltered = client.get("/api/v1/sessions").json()["origin_counts"]
    root_only = client.get("/api/v1/sessions", params={"origin": "root"}).json()["origin_counts"]
    subagent_only = client.get(
        "/api/v1/sessions", params={"origin": "subagent"}
    ).json()["origin_counts"]
    assert root_only == subagent_only == unfiltered


def test_sessions_origin_counts_respect_projects_filter(client: TestClient) -> None:
    body = client.get("/api/v1/sessions", params={"projects": "no-such-slug"}).json()
    assert body["origin_counts"] == {"root": 0, "subagent": 0, "empty": 0}

    body = client.get("/api/v1/sessions", params={"projects": ORIGIN_PROJECT_SLUG}).json()
    assert body["origin_counts"] == {"root": 1, "subagent": 1, "empty": 0}


def test_sessions_origin_counts_respect_favorite_filter(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{ORIGIN_ROOT_SESSION}/favorite")
    assert resp.status_code == 204
    body = client.get("/api/v1/sessions", params={"favorite": 1}).json()
    assert body["origin_counts"] == {"root": 1, "subagent": 0, "empty": 0}
