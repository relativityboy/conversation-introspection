"""Favorites endpoints (Task P2-7): PUT/DELETE /api/v1/sessions/{uuid}/favorite.

Same app-over-shared-db wiring as ``test_api_sessions.py``: the app is pointed at the SAME
SQLite file the ``db_session`` fixture writes to, so a test can favorite a session over HTTP
and then read the row back directly via ``db_session`` (or vice versa) with WAL
cross-connection visibility.

The last test is the spec Sec.4 "never touched by import/reparse" invariant: favoriting must
survive both ``run_import`` and ``reparse_all`` run against the very same db_path/root the app
uses, proven end-to-end rather than just by reading ``reparse_all``'s delete list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import run_import
from introspect.models import Favorite
from tests.conftest import SESSION_UUID_1, SESSION_UUID_2


def _capture(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    db.commit()


@pytest.fixture
def client(db_session: Session, fixture_tree: Path, tmp_path: Path) -> TestClient:
    """App over the pinned fixture tree, sharing ``db_session``'s DB file."""
    _capture(db_session, fixture_tree)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


def _favorite_of(body_items: list[dict], session_uuid: str) -> bool:
    return next(i["favorite"] for i in body_items if i["session_uuid"] == session_uuid)


# --- PUT ----------------------------------------------------------------------------------


def test_put_favorite_reflected_in_sessions_list(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    assert resp.status_code == 204
    assert resp.content == b""

    body = client.get("/api/v1/sessions").json()
    assert _favorite_of(body["items"], SESSION_UUID_1) is True
    assert _favorite_of(body["items"], SESSION_UUID_2) is False


def test_double_put_is_idempotent_one_row(
    db_session: Session, client: TestClient
) -> None:
    first = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    second = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    assert first.status_code == 204
    assert second.status_code == 204

    rows = db_session.query(Favorite).filter_by(session_uuid=SESSION_UUID_1).all()
    assert len(rows) == 1


def test_put_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.put("/api/v1/sessions/does-not-exist/favorite")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_favorite_filter_round_trips_after_put(client: TestClient) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")

    body = client.get("/api/v1/sessions", params={"favorite": 1}).json()
    assert [i["session_uuid"] for i in body["items"]] == [SESSION_UUID_1]
    assert body["total"] == 1


# --- DELETE ---------------------------------------------------------------------------------


def test_delete_favorite_reflected_in_sessions_list(client: TestClient) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")

    resp = client.delete(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    assert resp.status_code == 204
    assert resp.content == b""

    body = client.get("/api/v1/sessions").json()
    assert _favorite_of(body["items"], SESSION_UUID_1) is False


def test_delete_non_favorite_is_204(client: TestClient) -> None:
    resp = client.delete(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    assert resp.status_code == 204
    assert resp.content == b""


def test_delete_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.delete("/api/v1/sessions/does-not-exist/favorite")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


# --- Invariant guard: favorites survive import/reparse (spec Sec.4) -----------------------


def test_favorite_survives_import_and_reparse(
    db_session: Session, client: TestClient, fixture_tree: Path, tmp_path: Path
) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/favorite")
    before = db_session.query(Favorite).filter_by(session_uuid=SESSION_UUID_1).one()

    db_path = tmp_path / "archive.db"
    run_import(db_path, fixture_tree)
    reparse_all(db_session)

    after = db_session.query(Favorite).filter_by(session_uuid=SESSION_UUID_1).one()
    assert after.session_uuid == before.session_uuid
    assert after.created_at == before.created_at

    body = client.get("/api/v1/sessions").json()
    assert _favorite_of(body["items"], SESSION_UUID_1) is True
