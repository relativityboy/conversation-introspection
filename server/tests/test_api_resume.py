"""POST /api/v1/sessions/{uuid}/resume (spec §17.2).

Same wiring as test_api_archive.py: app over the pinned fixture tree sharing db_session's DB
file — but with fixture_tree as the app's SOURCE ROOT (resume reads AND writes it) and a fake
resume_runner so no test ever spawns `open`.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.export import export_transcript
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import Project
from tests.conftest import PROJECT_SLUG_1, SESSION_UUID_1


class FakeOpen:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        self.calls.append(argv)
        return (0, "", "")


@pytest.fixture
def fake_open() -> FakeOpen:
    return FakeOpen()


@pytest.fixture
def client(
    db_session: Session, fixture_tree: Path, tmp_path: Path, fake_open: FakeOpen, monkeypatch
) -> TestClient:
    monkeypatch.setattr(sys, "platform", "darwin")
    for f in discover(fixture_tree):
        capture_file(db_session, f)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    project = db_session.scalars(select(Project).where(Project.dir_slug == PROJECT_SLUG_1)).one()
    project.resolved_cwd = str(workdir)
    db_session.commit()
    app = create_app(
        db_path=tmp_path / "archive.db",
        source_root=fixture_tree,
        terminal_app="TestTerm",
        resume_runner=fake_open,
    )
    return TestClient(app)


def test_resume_unknown_session_404(client: TestClient) -> None:
    resp = client.post("/api/v1/sessions/00000000-0000-0000-0000-000000000000/resume")
    assert resp.status_code == 404


def test_resume_archived_session_404(client: TestClient) -> None:
    assert client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive").status_code == 204
    resp = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert resp.status_code == 404  # indistinguishable from nonexistent (§15.1)


def test_resume_present_file_launches(client: TestClient, fake_open: FakeOpen) -> None:
    resp = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["restored"] is False
    assert body["launched"] is True
    assert body["mode"] == "launched"
    assert body["command"] == f"claude --resume {SESSION_UUID_1}"
    assert len(fake_open.calls) == 1
    assert fake_open.calls[0][:3] == ["open", "-a", "TestTerm"]


def test_resume_restores_missing_file_byte_identical(
    client: TestClient, db_session: Session, fixture_tree: Path
) -> None:
    live = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl"
    expected = export_transcript(db_session, SESSION_UUID_1)
    live.unlink()
    body = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume").json()
    assert body["restored"] is True
    assert live.read_bytes() == expected


def test_on_disk_flips_after_restore(client: TestClient, fixture_tree: Path) -> None:
    live = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl"
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is True
    live.unlink()
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is False
    client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is True
