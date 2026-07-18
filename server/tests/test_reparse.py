"""Reparse tests (Task 9): rebuilding the interpretation layer from stored raw bytes.

The first three tests are the binding contract (verbatim from task-9-brief); the remainder
pin the amendments from the review rounds: a capture-phase integrity anomaly must survive
the interpretation-anomaly wipe, and a mid-chunk interpretation failure must not silently
discard its chunk-mates' already-staged work.
"""

from introspect.ingest import interpret
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.reparse import reparse_all
from introspect.models import ContentBlock, Message, ParseAnomaly, RawRecord, SessionEvent
from introspect.schema import SCHEMA_VERSION
from tests.fixtures.records import make_user_line
from tests.test_capture import _capture_all

# --- Binding contract (verbatim from task-9-brief) ---------------------------------------


def test_reparse_rebuilds_identical_interpretation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    before = {
        "messages": db_session.query(Message).count(),
        "blocks": db_session.query(ContentBlock).count(),
        "events": db_session.query(SessionEvent).count(),
    }
    stats = reparse_all(db_session)
    after = {
        "messages": db_session.query(Message).count(),
        "blocks": db_session.query(ContentBlock).count(),
        "events": db_session.query(SessionEvent).count(),
    }
    assert before == after and stats.records_reparsed > 0


def test_reparse_needs_no_source_files(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    import shutil
    shutil.rmtree(fixture_tree)
    stats = reparse_all(db_session)
    assert stats.records_reparsed > 0


def test_reparse_updates_schema_version_stamp(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    db_session.query(RawRecord).update({"parsed_with_schema_version": "introspect-schema/0"})
    reparse_all(db_session)
    versions = {v for (v,) in db_session.query(RawRecord.parsed_with_schema_version).distinct()}
    assert versions == {SCHEMA_VERSION}


# --- Amendments from the review rounds ----------------------------------------------------


def test_reparse_preserves_capture_phase_anomaly(db_session, fixture_tree):
    """A capture-phase integrity anomaly (source_diverged) is history reparse cannot
    regenerate from raw bytes alone -- it must survive the interpretation-anomaly wipe."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-reparse1"}\n'
        + content[content.index(b"\n") + 1 :]
    )
    _capture_all(db_session, fixture_tree)
    before = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()

    reparse_all(db_session)

    after = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()
    assert after.id == before.id
    assert after.detail == before.detail


def test_reparse_is_status_idempotent_including_whitespace_lines(db_session, fixture_tree):
    """A no-op reparse of an unchanged archive must not mutate parse statuses or anomaly
    grading. The sharp edge is a whitespace-only line: capture grades it 'whitespace_line'
    (info, partial) without ever handing it to parse_line, and reparse must grade it the
    same way -- not regrade it as 'invalid_json' (error, anomaly)."""
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(b"   \n")  # torn-write residue: whitespace, complete line
    _capture_all(db_session, fixture_tree)

    def snapshot():
        statuses = dict(
            db_session.query(RawRecord.id, RawRecord.parse_status).all()
        )
        anomalies = sorted(
            (a.kind, a.severity) for a in db_session.query(ParseAnomaly).all()
        )
        return statuses, anomalies

    captured_state = snapshot()
    reparse_all(db_session)
    assert snapshot() == captured_state
    reparse_all(db_session)
    assert snapshot() == captured_state


def test_reparse_isolates_a_failing_record_without_losing_chunk_mates(
    db_session, tmp_path, monkeypatch
):
    """A mid-chunk interpretation failure must not silently drop its chunk-mates' work.

    Reparse batches a chunk of 500 records into one commit; without a per-record SAVEPOINT a
    later record raising would roll back everyone processed earlier in the same chunk, even
    though those records were never touched by the actual bug.
    """
    proj = tmp_path / "isolation" / "-Users-x-iso"
    proj.mkdir(parents=True)
    session_uuid = "44444444-4444-4444-4444-444444444444"
    lines = b"".join(make_user_line(text=f"line {i}", sessionId=session_uuid) for i in range(3))
    (proj / f"{session_uuid}.jsonl").write_bytes(lines)
    for f in discover(tmp_path / "isolation"):
        capture_file(db_session, f)
    db_session.commit()
    assert db_session.query(Message).count() == 3

    real_apply = interpret.apply
    calls = {"n": 0}

    def flaky(db, pr, raw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic reparse failure")
        return real_apply(db, pr, raw)

    monkeypatch.setattr(interpret, "apply", flaky)
    stats = reparse_all(db_session)

    assert stats.records_reparsed == 3
    assert db_session.query(ParseAnomaly).filter_by(kind="interpret_failure").count() == 1
    # Records 1 and 3 (chunk-mates of the deliberately-failed record 2) kept their staged
    # work: the SAVEPOINT isolated the failure instead of losing the whole chunk.
    assert db_session.query(Message).count() == 2
