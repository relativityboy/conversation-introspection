"""Route tests for ``GET /api/v1/memories`` (memories board Task 1).

Uses the shared ``db_session``/``create_app(db_path=...)`` pairing (see
``tests/test_api_admin.py``): both point at the same ``tmp_path / "archive.db"`` so rows
inserted directly through the ORM session are visible over HTTP. ``source_root`` always
points at a synthetic ``tmp_path`` tree -- never the real archive or transcripts."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import utcnow
from introspect.models import ExcludedProject, Project


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_memories_envelope_shape_and_resolved_cwd(db_session: Session, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(
        root / "proj-with-cwd" / "memory" / "note.md",
        "---\nname: N\ndescription: D\ntype: feedback\n---\nbody\n",
    )
    _write(root / "proj-no-row" / "memory" / "other.md", "# Other\n")

    db_session.add(
        Project(dir_slug="proj-with-cwd", resolved_cwd="/Users/x/proj", first_seen_at=utcnow())
    )
    db_session.commit()

    client = TestClient(create_app(db_path=tmp_path / "archive.db", source_root=root))
    resp = client.get("/api/v1/memories")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body) == {"projects"}
    by_slug = {p["dir_slug"]: p for p in body["projects"]}
    assert set(by_slug) == {"proj-with-cwd", "proj-no-row"}
    assert by_slug["proj-with-cwd"]["resolved_cwd"] == "/Users/x/proj"
    assert by_slug["proj-no-row"]["resolved_cwd"] is None

    note = next(m for m in by_slug["proj-with-cwd"]["memories"] if m["filename"] == "note.md")
    assert set(note) == {
        "name",
        "description",
        "type",
        "filename",
        "path",
        "size",
        "mtime",
        "body",
        "error",
    }
    assert note["name"] == "N"
    assert note["description"] == "D"
    assert note["type"] == "feedback"
    assert note["error"] is None


def test_excluded_project_absent_from_response(db_session: Session, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "hidden-proj" / "memory" / "x.md", "# X\n")

    db_session.add(ExcludedProject(dir_slug="hidden-proj", reason=None, created_at=utcnow()))
    db_session.commit()

    client = TestClient(create_app(db_path=tmp_path / "archive.db", source_root=root))
    resp = client.get("/api/v1/memories")
    assert resp.status_code == 200
    # hidden-proj is the ONLY project on disk: the envelope must be exactly empty, not just
    # missing that one slug -- pins that an excluded project is indistinguishable from a root
    # with nothing in it at all.
    assert resp.json() == {"projects": []}


def test_no_memory_dirs_returns_empty_projects(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    client = TestClient(create_app(db_path=tmp_path / "archive.db", source_root=root))
    resp = client.get("/api/v1/memories")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_nonexistent_source_root_returns_empty_projects(tmp_path: Path) -> None:
    root = tmp_path / "does-not-exist"

    client = TestClient(create_app(db_path=tmp_path / "archive.db", source_root=root))
    resp = client.get("/api/v1/memories")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_mtime_serializes_with_timezone_offset(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "proj" / "memory" / "a.md", "# A\n")

    client = TestClient(create_app(db_path=tmp_path / "archive.db", source_root=root))
    resp = client.get("/api/v1/memories")
    mtime = resp.json()["projects"][0]["memories"][0]["mtime"]
    assert mtime is not None
    assert mtime.endswith("+00:00") or mtime.endswith("Z")
