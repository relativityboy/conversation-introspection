"""FastAPI skeleton (Task P2-4): app factory, dependencies, problem-details errors.

No feature routers exist yet (Tasks 5-8 add them) -- these tests cover only the scaffolding
every future route depends on: ``/health``, the error-handler shapes, the ``get_db``
dependency, and the ``serve`` CLI command that boots uvicorn against ``create_app``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.api.deps import get_db
from introspect.cli import main
from introspect.models import Project


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(db_path=tmp_path / "archive.db")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers["content-type"].startswith("application/json")


def test_unknown_path_is_problem_json_404(client: TestClient) -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_create_app_migrates_fresh_db(tmp_path: Path) -> None:
    dbp = tmp_path / "fresh.db"
    assert not dbp.exists()
    create_app(db_path=dbp)
    assert dbp.exists()


def test_lookup_error_route_is_404_problem(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_test/lookup-error")
    def _raise_lookup() -> None:
        raise LookupError("session not found")

    resp = client.get("/api/v1/_test/lookup-error")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}


def test_unhandled_exception_is_500_with_class_name_only_detail(app: FastAPI) -> None:
    @app.get("/api/v1/_test/boom")
    def _raise_boom() -> None:
        raise RuntimeError("some sensitive internal detail that must never leak")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/_test/boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["detail"] == "RuntimeError"
    assert "sensitive internal detail" not in resp.text


def test_get_db_dependency_yields_working_session(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_test/db-count")
    def _count(db: Session = Depends(get_db)) -> dict[str, int]:
        return {"projects": db.query(Project).count()}

    resp = client.get("/api/v1/_test/db-count")
    assert resp.status_code == 200
    assert resp.json() == {"projects": 0}


# --- CLI `serve` wiring --------------------------------------------------------------------


def test_serve_invokes_uvicorn_with_configured_app(tmp_path, monkeypatch) -> None:
    calls = {}

    def fake_run(app: FastAPI, host: str | None = None, port: int | None = None) -> None:
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("introspect.cli.uvicorn.run", fake_run)
    dbp = str(tmp_path / "a.db")
    assert main(["serve", "--db", dbp, "--host", "0.0.0.0", "--port", "9999"]) == 0
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9999
    assert isinstance(calls["app"], FastAPI)


def test_serve_defaults_to_localhost_and_default_port(tmp_path, monkeypatch) -> None:
    calls = {}
    monkeypatch.setattr(
        "introspect.cli.uvicorn.run",
        lambda app, host=None, port=None: calls.update(host=host, port=port),
    )
    assert main(["serve", "--db", str(tmp_path / "a.db")]) == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8765


def test_serve_db_open_failure_exits_2(tmp_path, capsys) -> None:
    bad_db = tmp_path / "not_a_file.db"
    bad_db.mkdir()
    assert main(["serve", "--db", str(bad_db)]) == 2
    assert capsys.readouterr().err
