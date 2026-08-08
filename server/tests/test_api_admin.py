"""Admin endpoints (Task P2-8): import trigger, run history, status, anomalies, export.

These exercise the real orchestrator and query layers over the pinned fixture tree. Two DB
lifecycles appear:

* Read tests (export/status/anomalies/runs) stage rows through the ``db_session`` fixture (the
  same ``tmp_path/archive.db`` the app opens) and read them back over HTTP.
* The import-trigger test starts from an EMPTY DB and lets ``POST /api/v1/import`` populate it,
  pointing ``create_app(source_root=...)`` at the fixture tree so the worker never scans real
  transcripts. The worker Thread is exposed on ``app.state.last_import_thread`` and joined
  before teardown so the tmp DB is never deleted out from under a live writer.
"""

from __future__ import annotations

import fcntl
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.export import export_transcript, iter_transcript_lines
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.run import DbOpenError
from introspect.models import ImportRun, ParseAnomaly, SourceFile
from tests.conftest import SESSION_UUID_1, TOTAL_FIXTURE_LINES
from tests.fixtures.records import make_user_line


def _capture(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    db.commit()


# --- Export -----------------------------------------------------------------------------


def test_export_bytes_match_export_transcript_and_headers(
    db_session: Session, fixture_tree: Path, tmp_path: Path
) -> None:
    _capture(db_session, fixture_tree)
    expected = export_transcript(db_session, SESSION_UUID_1)

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get(f"/api/v1/sessions/{SESSION_UUID_1}/export.jsonl")

    assert resp.status_code == 200
    assert resp.content == expected  # byte-identical to the CLI export path
    assert resp.headers["content-type"] == "application/x-ndjson"
    assert (
        resp.headers["content-disposition"]
        == f'attachment; filename="{SESSION_UUID_1}.jsonl"'
    )


def test_export_bak_only_transcript_streams_fallback_bytes(
    db_session: Session, tmp_path: Path
) -> None:
    """The no-primary fallback (bak-only transcript) works on the streaming path too: both
    iter_transcript_lines and the endpoint hand back the backup's exact bytes."""
    root = tmp_path / "r"
    slug = root / "-Users-x-proj"
    slug.mkdir(parents=True)
    content = make_user_line(text="only the backup survived")
    session_uuid = "cccccccc-1111-2222-3333-444444444444"
    (slug / f"{session_uuid}.jsonl.bak-1700000000").write_bytes(content)
    _capture(db_session, root)

    assert b"".join(iter_transcript_lines(db_session, session_uuid)) == content

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get(f"/api/v1/sessions/{session_uuid}/export.jsonl")
    assert resp.status_code == 200
    assert resp.content == content


def test_export_unknown_session_is_404_problem(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get("/api/v1/sessions/does-not-exist/export.jsonl")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


# --- Import trigger ---------------------------------------------------------------------


def test_import_trigger_runs_to_completion(tmp_path: Path, fixture_tree: Path) -> None:
    client = TestClient(
        create_app(db_path=tmp_path / "archive.db", source_root=fixture_tree)
    )

    resp = client.post("/api/v1/import")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert isinstance(run_id, int)

    # Poll the run row until it leaves 'running' (bounded ~5s).
    deadline = time.time() + 5.0
    row: dict = {}
    while time.time() < deadline:
        row = client.get(f"/api/v1/import/runs/{run_id}").json()
        if row["status"] != "running":
            break
        time.sleep(0.1)

    # Join the worker before teardown deletes the tmp DB under a live writer.
    client.app.state.last_import_thread.join(timeout=5)

    row = client.get(f"/api/v1/import/runs/{run_id}").json()
    assert row["status"] == "ok"
    assert row["files_seen"] == 5
    assert row["records_added"] == TOTAL_FIXTURE_LINES
    assert row["trigger"] == "api"
    assert row["finished_at"] is not None


def test_import_lock_held_returns_409_and_writes_no_row(
    tmp_path: Path, fixture_tree: Path
) -> None:
    client = TestClient(
        create_app(db_path=tmp_path / "archive.db", source_root=fixture_tree)
    )
    lock = tmp_path / "import.lock"
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        resp = client.post("/api/v1/import")

    assert resp.status_code == 409
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["title"] == "import already running"

    # No ImportRun row was written under the contended probe.
    runs = client.get("/api/v1/import/runs").json()
    assert runs["total"] == 0


def test_import_db_open_failure_finalizes_row_fatal(
    tmp_path: Path, fixture_tree: Path, monkeypatch
) -> None:
    """DbOpenError from the worker's run_import must not strand the pre-created row 'running':
    the thread wrapper's best-effort finalize stamps it 'fatal' (the DB is fine in-test, so
    that attempt succeeds)."""
    client = TestClient(
        create_app(db_path=tmp_path / "archive.db", source_root=fixture_tree)
    )

    def boom(*args, **kwargs):
        raise DbOpenError("synthetic db-open failure")

    monkeypatch.setattr("introspect.api.routes.admin.run_import", boom)

    resp = client.post("/api/v1/import")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    deadline = time.time() + 5.0
    row: dict = {}
    while time.time() < deadline:
        row = client.get(f"/api/v1/import/runs/{run_id}").json()
        if row["status"] != "running":
            break
        time.sleep(0.1)

    client.app.state.last_import_thread.join(timeout=5)

    row = client.get(f"/api/v1/import/runs/{run_id}").json()
    assert row["status"] == "fatal"
    assert row["finished_at"] is not None


# --- Run history ------------------------------------------------------------------------


def test_runs_list_desc_and_pagination(db_session: Session, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    for _ in range(3):
        db_session.add(ImportRun(trigger="cli", started_at=now, status="ok"))
    db_session.commit()

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    body = client.get("/api/v1/import/runs").json()
    ids = [r["id"] for r in body["items"]]
    assert ids == sorted(ids, reverse=True)  # id DESC
    assert body["total"] == 3
    first = body["items"][0]
    assert set(first) == {
        "id",
        "trigger",
        "status",
        "started_at",
        "finished_at",
        "files_seen",
        "records_added",
        "records_skipped_duplicate",
        "anomaly_count",
    }

    page = client.get("/api/v1/import/runs", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] == ids[1]


def test_run_detail_unknown_id_is_404_problem(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get("/api/v1/import/runs/999999")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


# --- Status -----------------------------------------------------------------------------


def test_status_shape_and_counts(
    db_session: Session, fixture_tree: Path, tmp_path: Path
) -> None:
    _capture(db_session, fixture_tree)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    body = client.get("/api/v1/status").json()
    assert set(body) == {
        "version",
        "sessions",
        "files",
        "records",
        "archive_bytes",
        "anomalies",
        "last_run",
    }
    assert body["sessions"] == 3
    assert body["files"] == 5
    assert body["records"] == TOTAL_FIXTURE_LINES
    assert body["archive_bytes"] > 0
    assert set(body["anomalies"]) == {"error", "warn", "info"}
    # Captured directly (not via run_import) so there is no ImportRun row.
    assert body["last_run"] is None


def test_status_reports_injected_app_version(tmp_path: Path) -> None:
    client = TestClient(
        create_app(db_path=tmp_path / "archive.db", app_version="9.9.9")
    )
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert resp.json()["version"] == "9.9.9"


def test_app_version_defaults_to_changelog_lookup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("introspect.api.changelog.app_version", lambda: "7.7.7")
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert resp.json()["version"] == "7.7.7"


# --- Anomalies --------------------------------------------------------------------------


def test_anomalies_filter_and_unknown_severity_422(
    db_session: Session, fixture_tree: Path, tmp_path: Path
) -> None:
    _capture(db_session, fixture_tree)
    source_file_id = db_session.query(SourceFile.id).order_by(SourceFile.id).first()[0]
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            ParseAnomaly(
                raw_record_id=None,
                source_file_id=source_file_id,
                severity="error",
                kind="test_error",
                detail={"path": "x"},
                schema_version=None,
                created_at=now,
            ),
            ParseAnomaly(
                raw_record_id=None,
                source_file_id=None,
                severity="warn",
                kind="test_warn",
                detail={},
                schema_version=None,
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    all_body = client.get("/api/v1/anomalies").json()
    assert all_body["total"] >= 2
    ids = [a["id"] for a in all_body["items"]]
    assert ids == sorted(ids, reverse=True)  # id DESC
    item = all_body["items"][0]
    assert set(item) == {
        "id",
        "severity",
        "kind",
        "detail",
        "source_file_path",
        "created_at",
    }

    err = client.get("/api/v1/anomalies", params={"severity": "error"}).json()
    assert err["total"] >= 1
    assert all(a["severity"] == "error" for a in err["items"])
    # The error anomaly carries its joined source_file_path.
    assert any(a["source_file_path"] for a in err["items"])

    bad = client.get("/api/v1/anomalies", params={"severity": "bogus"})
    assert bad.status_code == 422
    assert set(bad.json()) == {"status", "title", "detail"}
