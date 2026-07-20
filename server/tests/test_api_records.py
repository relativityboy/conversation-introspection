"""Raw-record inspector endpoint (Task P4-F4, spec §15.2): GET /records/{record_uuid}/raw.

Same app-over-shared-db wiring as ``test_api_archive.py``: the app is pointed at the SAME SQLite
file the ``db_session`` fixture writes to, so a test can read the stored ``raw_line`` bytes back
directly via ``db_session`` and assert the endpoint returns them BYTE-IDENTICALLY (WAL
cross-connection visibility).

The endpoint's one job is byte-faithfulness: hand back the exact stored ``raw_records.raw_line``,
never re-parsed or re-serialized. It is served as ``text/plain`` (NOT ``application/json``) so a
malformed line comes back verbatim without the content-type lying about it. A record whose owning
session is archived 404s, folded into the SAME not-found as an unknown uuid (§15.1 read-exclusion,
mirroring ``list_messages``' archived probe).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file, utcnow
from introspect.ingest.discovery import discover
from introspect.models import ArchivedSession, Message, RawRecord, SourceFile, Transcript
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


def _a_record_uuid(db: Session, session_uuid: str) -> str:
    """The first Message's record_uuid in a session's MAIN transcript (a real uuid-bearing row)."""
    uuid = db.execute(
        select(Message.record_uuid)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(Transcript.session_id == session_uuid, Transcript.kind == "main")
        .order_by(Message.id)
    ).scalars().first()
    assert uuid is not None
    return uuid


def _raw_line_for(db: Session, record_uuid: str) -> bytes:
    line = db.execute(
        select(RawRecord.raw_line).where(RawRecord.record_uuid == record_uuid)
    ).scalars().first()
    assert line is not None
    return line


# --- byte-faithful success --------------------------------------------------------------


def test_raw_returns_exact_stored_bytes(db_session: Session, client: TestClient) -> None:
    uuid = _a_record_uuid(db_session, SESSION_UUID_1)
    expected = _raw_line_for(db_session, uuid)

    resp = client.get(f"/api/v1/records/{uuid}/raw")
    assert resp.status_code == 200
    # BYTE-identical: the response body equals the stored raw_line, never re-serialized.
    assert resp.content == expected
    # text/plain, NOT application/json -- the line may not be valid JSON at all.
    assert resp.headers["content-type"].startswith("text/plain")


def test_raw_malformed_line_is_byte_identical(
    db_session: Session, client: TestClient
) -> None:
    """A record whose raw_line is NOT valid JSON must still come back verbatim -- the raw view is
    byte-faithful, and the endpoint never parses. Inserted directly on session 2's (unarchived)
    main transcript with a distinct line_number so the (source_file_id, line_number) uniqueness
    constraint is satisfied."""
    tid = db_session.execute(
        select(Transcript.id).where(
            Transcript.session_id == SESSION_UUID_2, Transcript.kind == "main"
        )
    ).scalars().first()
    sfid = db_session.execute(
        select(SourceFile.id).where(SourceFile.transcript_id == tid)
    ).scalars().first()
    assert tid is not None and sfid is not None

    malformed = b'{"type":"user","uuid":"malformed-0001","message":{ broken'
    db_session.add(
        RawRecord(
            source_file_id=sfid,
            transcript_id=tid,
            line_number=9999,
            byte_offset=0,
            raw_line=malformed,
            line_sha256="0" * 64,
            record_type="user",
            record_uuid="malformed-0001",
            detected_cli_version=None,
            parsed_with_schema_version=None,
            parse_status="anomaly",
            ingested_at=utcnow(),
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/records/malformed-0001/raw")
    assert resp.status_code == 200
    assert resp.content == malformed
    assert resp.headers["content-type"].startswith("text/plain")


# --- 404s -------------------------------------------------------------------------------


def test_raw_unknown_uuid_is_404_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/records/does-not-exist/raw")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 404


def test_raw_archived_session_record_is_404(
    db_session: Session, client: TestClient
) -> None:
    """§15.1: once the owning session is archived, its records 404 -- indistinguishable from an
    unknown uuid. Reachable before archiving, 404 after."""
    uuid = _a_record_uuid(db_session, SESSION_UUID_1)
    assert client.get(f"/api/v1/records/{uuid}/raw").status_code == 200

    db_session.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
    db_session.commit()

    resp = client.get(f"/api/v1/records/{uuid}/raw")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}
