"""Production serving (Phase 3 Task 9): FastAPI serves the built React UI alongside the API.

``create_app`` resolves a UI ``dist`` directory (param > ``INTROSPECT_UI_DIST`` env var >
walking up from the package looking for a repo checkout's ``web/dist/index.html`` > API-only)
and, when one is found, mounts its static assets and falls back to ``index.html`` for any
GET that doesn't match a route and doesn't start with ``api/`` -- the SPA client-side router
handles the rest. ``/api/v1/*`` never falls back to HTML; an unmatched API path stays a
problem-JSON 404 (:mod:`introspect.api.errors`), same as before this task.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from introspect.api import create_app

# Bound at import time -- i.e. BEFORE the autouse ``_no_ambient_ui_dist`` fixture
# (tests/conftest.py) monkeypatches the ``introspect.api`` module attribute -- so the walk
# unit tests below exercise the REAL implementation, not the fixture's stub.
from introspect.api import _walk_up_for_ui_dist as real_walk_up_for_ui_dist
from introspect.cli import main


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    """A minimal fake ``web/dist``: index.html, one hashed asset, and a root-level favicon."""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>reading room</body></html>")
    (assets / "app.js").write_text("console.log('hello');")
    (dist / "favicon.svg").write_text("<svg></svg>")
    return dist


@pytest.fixture
def app_with_ui(tmp_path: Path, fake_dist: Path) -> FastAPI:
    return create_app(db_path=tmp_path / "archive.db", ui_dist=fake_dist)


@pytest.fixture
def client_with_ui(app_with_ui: FastAPI) -> TestClient:
    return TestClient(app_with_ui)


# --- UI present: static assets + SPA fallback ----------------------------------------------


def test_root_serves_index_html(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "reading room" in resp.text


def test_head_root_serves_index_html(client_with_ui: TestClient) -> None:
    resp = client_with_ui.head("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_spa_route_falls_back_to_index_html(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/s/some-uuid-the-server-has-never-heard-of")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "reading room" in resp.text


def test_asset_is_served_with_js_content_type(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/assets/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert resp.text == "console.log('hello');"


def test_real_dist_root_file_is_served_directly(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/favicon.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers["content-type"]
    assert resp.text == "<svg></svg>"


def test_health_untouched_by_ui_mounting(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_api_path_stays_problem_json_404_not_index(client_with_ui: TestClient) -> None:
    resp = client_with_ui.get("/api/v1/nonexistent")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}


def test_post_to_unknown_api_path_is_not_index(client_with_ui: TestClient) -> None:
    resp = client_with_ui.post("/api/v1/nonexistent")
    assert resp.status_code in (404, 405)
    assert resp.headers["content-type"].startswith("application/json")


# --- UI absent: API-only mode, current behavior preserved --------------------------------


def test_no_dist_found_is_api_only_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ambient env + walk are already neutralized by the autouse fixture in conftest.py
    capsys.readouterr()  # drop anything earlier fixtures wrote
    app = create_app(db_path=tmp_path / "archive.db", ui_dist=None)
    assert app.state.ui_dist is None
    # factory purity: create_app itself never logs/prints, even in API-only mode --
    # the CLI owns the one line of UI logging (alembic logs via logging, not stdout/stderr)
    captured = capsys.readouterr()
    assert "UI" not in captured.out and "UI" not in captured.err
    client = TestClient(app)

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    resp = client.get("/")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}


def test_broken_dist_missing_assets_degrades_to_api_only(tmp_path: Path) -> None:
    """index.html without assets/ is a broken dist: boot API-only, never crash."""
    broken = tmp_path / "broken_dist"
    broken.mkdir()
    (broken / "index.html").write_text("<!doctype html><html><body>broken</body></html>")

    app = create_app(db_path=tmp_path / "archive.db", ui_dist=broken)
    assert app.state.ui_dist is None
    client = TestClient(app)

    assert client.get("/api/v1/health").status_code == 200
    resp = client.get("/")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


# --- UI dist resolution precedence ----------------------------------------------------------


def test_env_var_is_used_when_no_param_given(
    tmp_path: Path, fake_dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTROSPECT_UI_DIST", str(fake_dist))
    app = create_app(db_path=tmp_path / "archive.db")
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "reading room" in resp.text


def test_param_wins_over_env_var(
    tmp_path: Path, fake_dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_dist = tmp_path / "other_dist"
    other_dist.mkdir()
    (other_dist / "index.html").write_text("<!doctype html><html><body>other</body></html>")
    monkeypatch.setenv("INTROSPECT_UI_DIST", str(other_dist))

    app = create_app(db_path=tmp_path / "archive.db", ui_dist=fake_dist)
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "reading room" in resp.text


# --- The upward walk itself (real implementation, bound before the autouse stub) -----------


def test_walk_finds_complete_dist_and_stops_at_git_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    dist = repo / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    start = repo / "server" / "src" / "introspect" / "api"
    start.mkdir(parents=True)

    assert real_walk_up_for_ui_dist(start) == dist


def test_walk_rejects_dist_missing_assets_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    dist = repo / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")  # no assets/ -> half-built
    start = repo / "server" / "src" / "introspect" / "api"
    start.mkdir(parents=True)

    assert real_walk_up_for_ui_dist(start) is None


def test_walk_never_ascends_past_git_boundary(tmp_path: Path) -> None:
    """A valid dist ABOVE the repo root must never be picked up."""
    outer_dist = tmp_path / "web" / "dist"
    (outer_dist / "assets").mkdir(parents=True)
    (outer_dist / "index.html").write_text("<!doctype html>")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    start = repo / "server" / "src" / "introspect" / "api"
    start.mkdir(parents=True)

    assert real_walk_up_for_ui_dist(start) is None


# --- CLI `serve` logs UI presence -----------------------------------------------------------


def test_serve_logs_ui_serving_when_dist_found(
    tmp_path: Path, fake_dist: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("INTROSPECT_UI_DIST", str(fake_dist))
    monkeypatch.setattr("introspect.cli.uvicorn.run", lambda app, host=None, port=None: None)

    assert main(["serve", "--db", str(tmp_path / "a.db")]) == 0
    assert "UI: serving web/dist" in capsys.readouterr().out


def test_serve_logs_api_only_when_no_dist_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # ambient env + walk are already neutralized by the autouse fixture in conftest.py
    monkeypatch.setattr("introspect.cli.uvicorn.run", lambda app, host=None, port=None: None)

    assert main(["serve", "--db", str(tmp_path / "a.db")]) == 0
    assert "UI: not built (API only)" in capsys.readouterr().out


# --- Cache policy (Task 7) -----------------------------------------------------------------------


def test_index_and_spa_fallback_are_no_cache(client_with_ui: TestClient) -> None:
    # index.html must not be cached forever since it's not content-hashed
    assert client_with_ui.get("/").headers["cache-control"] == "no-cache"
    # SPA fallback also gets no-cache so a stale cache doesn't hide a fresh build
    assert client_with_ui.get("/some/spa/route").headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable(client_with_ui: TestClient) -> None:
    # Vite content-hashes asset filenames, so they are safe to cache forever
    resp = client_with_ui.get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
