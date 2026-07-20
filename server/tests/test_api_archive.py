"""Archive endpoint + read-path exclusions (Task P4-F3, spec §15.1).

Same app-over-shared-db wiring as ``test_api_favorites.py``: the app is pointed at the SAME
SQLite file the ``db_session`` fixture writes to, so a test can archive a session over HTTP and
then read the row back directly via ``db_session`` (WAL cross-connection visibility).

An archived session is existence-based like ``Favorite``/``UserTitle`` (see ``ArchivedSession``
in :mod:`introspect.models`): a session is archived iff an ``archived_sessions`` row exists.
``PUT /archive`` is the ONLY verb (idempotent, 204); there is no API/UI unarchive -- restore is
CLI-only (``introspect unarchive``), covered in ``test_cli.py``. Once archived, EVERY API read
path hides the session: list (absent), detail (404), messages (404 via the owning transcript),
export (404), and search (hits filtered). Capture/reparse keep syncing it untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.export import export_transcript
from introspect.ingest.capture import capture_file, utcnow
from introspect.ingest.discovery import discover
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import run_import
from introspect.models import ArchivedSession, RawRecord, Transcript
from introspect.search import get_search_index
from tests.conftest import PROJECT_SLUG_1, SESSION_UUID_1, SESSION_UUID_2
from tests.fixtures.records import make_user_line


def _capture(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    db.commit()


@pytest.fixture
def client(db_session: Session, fixture_tree: Path, tmp_path: Path) -> TestClient:
    """App over the pinned fixture tree, sharing ``db_session``'s DB file."""
    _capture(db_session, fixture_tree)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


def _uuids(body_items: list[dict]) -> list[str]:
    return [i["session_uuid"] for i in body_items]


def _main_transcript_id(db: Session, session_uuid: str) -> int:
    return db.query(Transcript.id).filter(
        Transcript.session_id == session_uuid, Transcript.kind == "main"
    ).scalar()


def _main_record_count(db: Session, session_uuid: str) -> int:
    # commit() first so the query starts a fresh snapshot and sees any rows another connection
    # (run_import) committed since this session last read.
    db.commit()
    return db.query(func.count(RawRecord.id)).join(
        Transcript, RawRecord.transcript_id == Transcript.id
    ).filter(Transcript.session_id == session_uuid, Transcript.kind == "main").scalar()


# --- PUT /archive: idempotent hide ------------------------------------------------------


def test_put_archive_removes_session_from_list(client: TestClient) -> None:
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    assert resp.status_code == 204
    assert resp.content == b""

    body = client.get("/api/v1/sessions").json()
    assert SESSION_UUID_1 not in _uuids(body["items"])
    assert SESSION_UUID_2 in _uuids(body["items"])


def test_put_archive_makes_detail_404(client: TestClient) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    resp = client.get(f"/api/v1/sessions/{SESSION_UUID_1}")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_double_put_archive_is_idempotent_one_row(
    db_session: Session, client: TestClient
) -> None:
    first = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    second = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    assert first.status_code == 204
    assert second.status_code == 204

    rows = db_session.query(ArchivedSession).filter_by(session_uuid=SESSION_UUID_1).all()
    assert len(rows) == 1


def test_put_archive_unknown_session_is_404_problem(client: TestClient) -> None:
    resp = client.put("/api/v1/sessions/does-not-exist/archive")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_archive_total_excludes_hidden_session(client: TestClient) -> None:
    before = client.get("/api/v1/sessions").json()["total"]
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    after = client.get("/api/v1/sessions").json()
    assert after["total"] == before - 1
    assert SESSION_UUID_1 not in _uuids(after["items"])


# --- Read-path exclusions: messages, export ---------------------------------------------


def test_archived_session_messages_route_404(
    db_session: Session, client: TestClient
) -> None:
    tid = _main_transcript_id(db_session, SESSION_UUID_1)
    # Reachable before archiving...
    assert client.get(f"/api/v1/transcripts/{tid}/messages").status_code == 200

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    resp = client.get(f"/api/v1/transcripts/{tid}/messages")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


def test_archived_session_export_404(client: TestClient) -> None:
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}/export.jsonl").status_code == 200

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    resp = client.get(f"/api/v1/sessions/{SESSION_UUID_1}/export.jsonl")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


# --- Read-path exclusions: search (route post-filter, no raw FTS SQL) --------------------


def test_archived_session_hidden_from_global_search(
    db_session: Session, client: TestClient
) -> None:
    get_search_index().rebuild(db_session)
    db_session.commit()

    # "horizon" lives only in session 1's MAIN content: one group before archiving.
    before = client.get("/api/v1/search", params={"q": "horizon"}).json()
    assert [g["session"]["session_uuid"] for g in before["groups"]] == [SESSION_UUID_1]

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    after = client.get("/api/v1/search", params={"q": "horizon"}).json()
    assert after["groups"] == []


def test_archived_session_with_content_match_absent_from_sidebar_q(
    db_session: Session, client: TestClient
) -> None:
    """The §14.1 sidebar content search unions FTS content matches into GET /sessions?q=. The
    outer archived-exclusion must still hide a content-matched archived session -- proving the
    exclusion sits OUTSIDE the OR-predicate, not merely on the title branch."""
    get_search_index().rebuild(db_session)
    db_session.commit()

    before = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    assert _uuids(before["items"]) == [SESSION_UUID_1]  # matched by content

    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    after = client.get("/api/v1/sessions", params={"q": "horizon"}).json()
    assert SESSION_UUID_1 not in _uuids(after["items"])
    assert after["total"] == 0


# --- Admin paths keep working: CLI export ignores archive (§15.1 by design) ---------------


def test_cli_export_ignores_archive(
    db_session: Session, fixture_tree: Path, tmp_path: Path
) -> None:
    """CLI/direct-DB export is an ADMIN path -- it reconstructs raw bytes regardless of the
    archived flag (spec brief: leave it working). Only the API read paths hide archived sessions.
    Proven at the ``export`` module the CLI calls, which never consults ``archived_sessions``."""
    _capture(db_session, fixture_tree)
    db_session.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
    db_session.commit()

    original = (fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl").read_bytes()
    assert export_transcript(db_session, SESSION_UUID_1) == original


# --- Invariants: archived is user-data (survives import/reparse) + keeps syncing ---------


def test_archived_session_survives_import_and_reparse(
    db_session: Session, client: TestClient, fixture_tree: Path, tmp_path: Path
) -> None:
    client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    before = db_session.query(ArchivedSession).filter_by(session_uuid=SESSION_UUID_1).one()

    db_path = tmp_path / "archive.db"
    run_import(db_path, fixture_tree)
    reparse_all(db_session)

    after = db_session.query(ArchivedSession).filter_by(session_uuid=SESSION_UUID_1).one()
    assert after.session_uuid == before.session_uuid
    assert after.created_at == before.created_at

    # Still hidden after a full import + reparse cycle.
    body = client.get("/api/v1/sessions").json()
    assert SESSION_UUID_1 not in _uuids(body["items"])


def test_archived_session_keeps_syncing_new_records_while_hidden(
    db_session: Session, fixture_tree: Path, tmp_path: Path
) -> None:
    """§15.1: archiving prevents READ only -- capture/sync CONTINUES. Archive a session, append a
    new line to its source file, re-import: its records grow yet it stays hidden from the list."""
    db_path = tmp_path / "archive.db"
    run_import(db_path, fixture_tree)

    db_session.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
    db_session.commit()
    before = _main_record_count(db_session, SESSION_UUID_1)

    main_file = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl"
    main_file.write_bytes(
        main_file.read_bytes()
        + make_user_line(text="a new line captured after archiving", sessionId=SESSION_UUID_1)
    )
    run_import(db_path, fixture_tree)

    after = _main_record_count(db_session, SESSION_UUID_1)
    assert after == before + 1  # the appended line was captured despite the archive

    client = TestClient(create_app(db_path=db_path))
    body = client.get("/api/v1/sessions").json()
    assert SESSION_UUID_1 not in _uuids(body["items"])
