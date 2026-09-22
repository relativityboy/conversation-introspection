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

from datetime import datetime, timezone
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
from tests.fixtures.records import make_assistant_line, make_session_file, make_user_line


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


# --- GET /records/{uuid}: reverse lookup (2026-08-23) -----------------------------------
# A bare record_uuid (a journal citation, a search hit noted long ago) resolves to the
# conversation that holds it -- session, project, transcript -- without knowing any of
# them first. Same read-exclusion as /raw: archived-session records 404 indistinguishably.


def test_record_meta_names_its_session(db_session: Session, client: TestClient) -> None:
    uuid = _a_record_uuid(db_session, SESSION_UUID_1)
    resp = client.get(f"/api/v1/records/{uuid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_uuid"] == SESSION_UUID_1
    assert body["project_slug"] == "-Users-x-proj"
    assert body["transcript_kind"] == "main"
    assert isinstance(body["transcript_id"], int)
    assert body["type"] in {"user", "assistant"}
    assert set(body) == {
        "record_uuid",
        "session_uuid",
        "project_slug",
        "transcript_id",
        "transcript_kind",
        "type",
        "timestamp",
    }


def test_record_meta_subagent_record_names_parent_session(
    db_session: Session, client: TestClient
) -> None:
    uuid = db_session.execute(
        select(Message.record_uuid)
        .join(Transcript, Message.transcript_id == Transcript.id)
        .where(Transcript.session_id == SESSION_UUID_1, Transcript.kind == "subagent")
        .order_by(Message.id)
    ).scalars().first()
    assert uuid is not None
    body = client.get(f"/api/v1/records/{uuid}").json()
    assert body["session_uuid"] == SESSION_UUID_1
    assert body["transcript_kind"] == "subagent"


def test_record_meta_unknown_uuid_is_404_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/records/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}


def test_record_meta_archived_session_record_is_404(
    db_session: Session, client: TestClient
) -> None:
    uuid = _a_record_uuid(db_session, SESSION_UUID_1)
    assert client.get(f"/api/v1/records/{uuid}").status_code == 200
    db_session.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
    db_session.commit()
    resp = client.get(f"/api/v1/records/{uuid}")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


# --- GET /records/by-message-id/{api_message_id}: reverse lookup (Task T2) -------------
# One API message (``message.id``) can span multiple JSONL lines, hence multiple Message
# rows -- this groups them. Same read-exclusion as /records/{uuid}: an archived session's
# records are folded out of the list, and no live records (unknown id, or every match
# archived) 404s exactly like the bare-uuid lookup above.


def test_by_message_id_returns_all_records_for_shared_id(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "shared-id-src"
    proj = root / "-Users-x-shared"
    proj.mkdir(parents=True)
    session_uuid = "44444444-4444-4444-4444-444444444444"
    api_message_id = "msg_shared_route_0001"
    lines = [
        make_user_line(sessionId=session_uuid, promptSource="typed"),
        make_assistant_line(
            text="first half", api_message_id=api_message_id, sessionId=session_uuid
        ),
        make_assistant_line(
            text="second half", api_message_id=api_message_id, sessionId=session_uuid
        ),
    ]
    (proj / f"{session_uuid}.jsonl").write_bytes(make_session_file(lines))
    _capture(db_session, root)

    resp = client.get(f"/api/v1/records/by-message-id/{api_message_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_message_id"] == api_message_id
    assert len(body["records"]) == 2

    # Ascending Message.id (ingest order) after dedupe.
    expected_uuids = (
        db_session.execute(
            select(Message.record_uuid)
            .where(Message.api_message_id == api_message_id)
            .order_by(Message.id)
        )
        .scalars()
        .all()
    )
    assert [r["record_uuid"] for r in body["records"]] == list(expected_uuids)

    for record in body["records"]:
        assert record["session_uuid"] == session_uuid
        assert record["project_slug"] == "-Users-x-shared"
        assert record["transcript_kind"] == "main"
        assert record["type"] == "assistant"
        assert set(record) == {
            "record_uuid",
            "session_uuid",
            "project_slug",
            "transcript_id",
            "transcript_kind",
            "type",
            "timestamp",
        }


def test_by_message_id_unknown_id_is_404_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/records/by-message-id/msg_does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}


def test_by_message_id_archived_session_is_404(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "archived-id-src"
    proj = root / "-Users-x-archived-msg"
    proj.mkdir(parents=True)
    session_uuid = "55555555-5555-5555-5555-555555555555"
    api_message_id = "msg_archived_route_0001"
    lines = [
        make_user_line(sessionId=session_uuid, promptSource="typed"),
        make_assistant_line(api_message_id=api_message_id, sessionId=session_uuid),
    ]
    (proj / f"{session_uuid}.jsonl").write_bytes(make_session_file(lines))
    _capture(db_session, root)

    assert client.get(f"/api/v1/records/by-message-id/{api_message_id}").status_code == 200

    db_session.add(ArchivedSession(session_uuid=session_uuid, created_at=utcnow()))
    db_session.commit()

    resp = client.get(f"/api/v1/records/by-message-id/{api_message_id}")
    assert resp.status_code == 404
    assert set(resp.json()) == {"status", "title", "detail"}


def test_by_message_id_dedupes_by_highest_message_id_per_record_uuid(
    db_session: Session, client: TestClient
) -> None:
    """Two Message rows sharing one record_uuid AND api_message_id -- what a divergence
    re-ingest would produce for an unchanged line, before ``remove_interpretation_for_
    source_file`` cleans up the superseded generation's rows (see routes/records.py's
    cross-generation comment on ``get_record_meta``) -- must collapse to the NEWEST
    (highest ``Message.id``) row. Built directly at the ORM layer: the real capture
    pipeline always deletes the superseded generation's Message rows before the new
    generation is (re-)ingested, so this exact state can't be produced end-to-end through
    capture today (see the T2 write-up) -- this pins the query's tie-break regardless of
    how the state arises.

    The older (lower id) row is deliberately given the LATER timestamp and the newer
    (higher id) row the EARLIER one, so a query that dedupes by timestamp instead of by
    Message.id would return the wrong row and fail this test.
    """
    tid = db_session.execute(
        select(Transcript.id).where(
            Transcript.session_id == SESSION_UUID_2, Transcript.kind == "main"
        )
    ).scalars().first()
    sfid = db_session.execute(
        select(SourceFile.id).where(SourceFile.transcript_id == tid)
    ).scalars().first()
    assert tid is not None and sfid is not None

    shared_uuid = "dedupe-test-0001"
    shared_api_id = "msg_dedupe_route_0001"

    def _add(line_number: int, timestamp: datetime) -> None:
        raw = RawRecord(
            source_file_id=sfid,
            transcript_id=tid,
            line_number=line_number,
            byte_offset=0,
            raw_line=b'{"synthetic":true}',
            line_sha256="0" * 64,
            record_type="assistant",
            record_uuid=shared_uuid,
            detected_cli_version=None,
            parsed_with_schema_version=None,
            parse_status="ok",
            ingested_at=utcnow(),
        )
        db_session.add(raw)
        db_session.flush()
        db_session.add(
            Message(
                raw_record_id=raw.id,
                transcript_id=tid,
                record_uuid=shared_uuid,
                parent_uuid=None,
                timestamp=timestamp,
                type="assistant",
                api_message_id=shared_api_id,
            )
        )
        db_session.commit()

    _add(9001, datetime(2026, 1, 2, tzinfo=timezone.utc))  # older generation, LATER timestamp
    _add(9002, datetime(2026, 1, 1, tzinfo=timezone.utc))  # newer generation, EARLIER timestamp

    resp = client.get(f"/api/v1/records/by-message-id/{shared_api_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    assert body["records"][0]["record_uuid"] == shared_uuid
    assert body["records"][0]["timestamp"].startswith("2026-01-01")
