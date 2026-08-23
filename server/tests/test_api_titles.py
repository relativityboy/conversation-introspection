"""User title endpoint (Task P4-1): ``PUT /api/v1/sessions/{uuid}/title``.

Mirrors ``test_api_favorites.py``'s structure and app-over-shared-db wiring: a user title is
existence-based like ``Favorite`` (see ``UserTitle`` in :mod:`introspect.models`), except a
PUT with an empty/whitespace title deletes the row instead of a separate DELETE verb -- there
is no ``DELETE /title`` endpoint, sending back the archive-derived title IS the delete.

The last "survives import/reparse" test mirrors ``test_favorite_survives_import_and_reparse``
exactly (spec Sec.4's "never touched" invariant, proven end-to-end). The final test proves
``user_title`` threads through the search endpoint's per-group ``SessionSummary`` -- search.py's
THIRD ``_summary`` caller (critique F1, blocker) -- not just sessions.py's two.
"""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.interpret import classify_pending
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import run_import
from introspect.models import UserTitle
from introspect.search import get_search_index
from tests.conftest import SESSION_UUID_1, SESSION_UUID_2


def _capture(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    # Production-shaped (import/reparse both classify): search's chat-default sources
    # filter on authorship, so an unclassified fixture tests a state production never has.
    classify_pending(db)
    db.commit()


@pytest.fixture
def client(db_session: Session, fixture_tree: Path, tmp_path: Path) -> TestClient:
    """App over the pinned fixture tree, sharing ``db_session``'s DB file."""
    _capture(db_session, fixture_tree)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


def _user_title_of(body_items: list[dict], session_uuid: str) -> str | None:
    return next(i["user_title"] for i in body_items if i["session_uuid"] == session_uuid)


# --- PUT: upsert ----------------------------------------------------------------------


def test_put_title_reflected_in_sessions_list(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "My Title"})
    assert resp.status_code == 204
    assert resp.content == b""

    body = client.get("/api/v1/sessions").json()
    assert _user_title_of(body["items"], SESSION_UUID_1) == "My Title"
    assert _user_title_of(body["items"], SESSION_UUID_2) is None


def test_put_title_reflected_in_session_detail(client: TestClient) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "My Title"})

    detail = client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    assert detail["user_title"] == "My Title"


def test_second_put_updates_title_and_updated_at_one_row(
    db_session: Session, client: TestClient
) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "First"})
    first = db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).one()

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "Second"})
    db_session.expire_all()
    second = db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).one()

    rows = db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).all()
    assert len(rows) == 1
    assert second.title == "Second"
    assert second.updated_at >= first.updated_at


def test_put_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.put("/api/v1/sessions/does-not-exist/title", json={"title": "x"})
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_put_title_over_200_chars_is_422_problem(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "x" * 201})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 422


def test_put_title_exactly_200_chars_is_accepted(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "x" * 200})
    assert resp.status_code == 204


# --- PUT: empty/whitespace title deletes -----------------------------------------------


def test_put_empty_title_deletes_row(client: TestClient, db_session: Session) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "Something"})

    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "   "})
    assert resp.status_code == 204
    assert resp.content == b""

    assert (
        db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).one_or_none() is None
    )
    body = client.get("/api/v1/sessions").json()
    assert _user_title_of(body["items"], SESSION_UUID_1) is None


def test_put_empty_title_when_absent_is_still_204(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": ""})
    assert resp.status_code == 204
    assert resp.content == b""


def test_put_long_whitespace_title_still_deletes_not_422(client: TestClient) -> None:
    """The 200-char cap validates the raw string, but only once the empty/whitespace decision
    (via ``.strip()``) has already ruled the title non-empty -- a long whitespace-only title is
    still a delete, not a 422 (brief: "cap applies post-strip decision")."""
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "Something"})

    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": " " * 250})
    assert resp.status_code == 204


def test_delete_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.put("/api/v1/sessions/does-not-exist/title", json={"title": ""})
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


# --- Invariant guard: user_title survives import/reparse (spec Sec.4) ------------------


def test_user_title_survives_import_and_reparse(
    db_session: Session, client: TestClient, fixture_tree: Path, tmp_path: Path
) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "Keeper"})
    before = db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).one()

    db_path = tmp_path / "archive.db"
    run_import(db_path, fixture_tree)
    reparse_all(db_session)

    after = db_session.query(UserTitle).filter_by(session_uuid=SESSION_UUID_1).one()
    assert after.session_uuid == before.session_uuid
    assert after.title == before.title
    assert after.updated_at == before.updated_at

    body = client.get("/api/v1/sessions").json()
    assert _user_title_of(body["items"], SESSION_UUID_1) == "Keeper"


# --- Search group header carries user_title (critique F1, blocker) ---------------------


def test_search_group_header_carries_user_title(
    db_session: Session, client: TestClient
) -> None:
    get_search_index().rebuild(db_session)
    db_session.commit()

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "Renamed"})

    resp = client.get("/api/v1/search", params={"q": "horizon"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"][0]["session"]["session_uuid"] == SESSION_UUID_1
    assert body["groups"][0]["session"]["user_title"] == "Renamed"


# --- PUT ?import_if_missing=true: capture-then-title (2026-08-23, /session-name) --------
#
# The running agent names ITS OWN session, which the hourly import usually has not captured
# yet. The flag makes the endpoint run the import in-request and retry the lookup, so the
# skill is one call instead of a PUT/POST/poll/PUT dance. Every app here is built over an
# EMPTY DB with ``source_root=fixture_tree`` (never the real ~/.claude/projects) -- the
# "not yet captured" state is the point of the test, not a fixture accident.


def _uncaptured_client(tmp_path: Path, fixture_tree: Path) -> TestClient:
    return TestClient(create_app(db_path=tmp_path / "archive.db", source_root=fixture_tree))


def _runs_total(client: TestClient) -> int:
    return client.get("/api/v1/import/runs").json()["total"]


def test_import_if_missing_imports_then_titles(tmp_path: Path, fixture_tree: Path) -> None:
    client = _uncaptured_client(tmp_path, fixture_tree)
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").status_code == 404

    resp = client.put(
        f"/api/v1/sessions/{SESSION_UUID_1}/title",
        params={"import_if_missing": "true"},
        json={"title": "the day we named sessions"},
    )
    assert resp.status_code == 204

    detail = client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    assert detail["user_title"] == "the day we named sessions"
    runs = client.get("/api/v1/import/runs").json()
    assert runs["total"] == 1
    assert runs["items"][0]["trigger"] == "api"
    assert runs["items"][0]["status"] == "ok"


def test_import_if_missing_still_absent_after_import_is_404_naming_the_run(
    tmp_path: Path, fixture_tree: Path
) -> None:
    client = _uncaptured_client(tmp_path, fixture_tree)
    resp = client.put(
        "/api/v1/sessions/99999999-9999-9999-9999-999999999999/title",
        params={"import_if_missing": "true"},
        json={"title": "x"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert "after import run 1" in body["detail"]
    assert "excluded" in body["detail"]  # names the likely reasons, not just "not found"
    assert _runs_total(client) == 1


def test_flag_absent_unknown_session_never_imports(tmp_path: Path, fixture_tree: Path) -> None:
    client = _uncaptured_client(tmp_path, fixture_tree)
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/title", json={"title": "x"})
    assert resp.status_code == 404
    assert _runs_total(client) == 0


def test_import_if_missing_rejects_over_long_title_before_importing(
    tmp_path: Path, fixture_tree: Path
) -> None:
    client = _uncaptured_client(tmp_path, fixture_tree)
    resp = client.put(
        f"/api/v1/sessions/{SESSION_UUID_1}/title",
        params={"import_if_missing": "true"},
        json={"title": "x" * 201},
    )
    assert resp.status_code == 422
    assert _runs_total(client) == 0


def test_import_if_missing_empty_title_is_a_delete_and_never_imports(
    tmp_path: Path, fixture_tree: Path
) -> None:
    # An empty title means "unset"; there is nothing to capture on behalf of a delete.
    client = _uncaptured_client(tmp_path, fixture_tree)
    resp = client.put(
        f"/api/v1/sessions/{SESSION_UUID_1}/title",
        params={"import_if_missing": "true"},
        json={"title": "   "},
    )
    assert resp.status_code == 404
    assert _runs_total(client) == 0


def test_import_if_missing_waits_for_a_running_import(
    tmp_path: Path, fixture_tree: Path
) -> None:
    # Cron's import holds the advisory lock when the agent calls: the endpoint waits for it
    # to finish, then runs its own import and titles -- no 409 leaks to the skill.
    client = _uncaptured_client(tmp_path, fixture_tree)
    lock = tmp_path / "import.lock"
    fh = lock.open("w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Timer(0.4, lambda: (fcntl.flock(fh, fcntl.LOCK_UN), fh.close())).start()
    try:
        resp = client.put(
            f"/api/v1/sessions/{SESSION_UUID_1}/title",
            params={"import_if_missing": "true"},
            json={"title": "named while cron ran"},
        )
    finally:
        if not fh.closed:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
    assert resp.status_code == 204
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["user_title"] == (
        "named while cron ran"
    )
    assert _runs_total(client) == 1


def test_import_if_missing_lock_never_freed_is_409_problem(
    tmp_path: Path, fixture_tree: Path, monkeypatch
) -> None:
    from introspect.api.routes import titles

    monkeypatch.setattr(titles, "_IMPORT_WAIT_SECONDS", 0.3)
    client = _uncaptured_client(tmp_path, fixture_tree)
    lock = tmp_path / "import.lock"
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        resp = client.put(
            f"/api/v1/sessions/{SESSION_UUID_1}/title",
            params={"import_if_missing": "true"},
            json={"title": "x"},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["title"] == "import already running"  # same shape POST /import uses
    assert _runs_total(client) == 0
