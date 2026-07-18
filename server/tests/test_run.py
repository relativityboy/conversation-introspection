"""Orchestrator tests (Task 11): the ``run_import`` entry point.

The first four tests are the binding contract from task-11-brief; the remainder pin the
review-round amendments this composition is responsible for (the unparsed-row self-healing
sweep and the CaptureStats duplicate aggregation).

``test_bad_file_does_not_halt_run`` is adapted from the brief: see its docstring for why the
original "directory posing as a jsonl file" premise cannot reach the code under test.
"""

import fcntl

import pytest

from introspect.db import get_engine, session_factory
from introspect.ingest import run
from introspect.ingest.capture import utcnow
from introspect.ingest.run import ImportSummary, run_import
from introspect.models import ImportRun, ParseAnomaly, RawRecord, SessionEvent
from introspect.schema import SCHEMA_VERSION
from tests.conftest import TOTAL_FIXTURE_LINES

# --- Binding contract (verbatim from task-11-brief) --------------------------------------


def test_end_to_end_import(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    s = run_import(dbp, fixture_tree)
    assert s.status == "ok" and s.files_seen == 5 and s.records_added == TOTAL_FIXTURE_LINES
    s2 = run_import(dbp, fixture_tree)
    assert s2.records_added == 0


def test_lock_contention_returns_already_running(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    lock = dbp.parent / "import.lock"
    lock.touch()
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        s = run_import(dbp, fixture_tree)
        assert s.status == "already_running"


def test_bad_file_does_not_halt_run(tmp_path, fixture_tree, monkeypatch):
    """A capture failure on one file must not halt the run: it becomes a file-level error
    anomaly and the remaining files still import (amendment 4) -> status 'errors'.

    ADAPTED FROM THE BRIEF. The brief created a *directory* named ``<uuid>.jsonl`` to make
    ``open()`` raise. Verified empirically while implementing: discovery routes every directory
    through its subagent branch and skips one whose name is not a bare session UUID (a
    ``<uuid>.jsonl`` dir name is not a UUID), so such a directory is NEVER discovered and never
    reaches ``capture_file`` -- it cannot trigger the path under test. We inject the failure the
    same way ``test_reparse`` does (monkeypatch a dependency to raise) on the ``.bak`` file,
    which normally dedups to 0 added records, so ``records_added == TOTAL`` still holds.
    """
    real_capture = run.capture_file

    def flaky(db, f):
        if f.kind == "backup":
            raise OSError("synthetic ingest failure")
        return real_capture(db, f)

    monkeypatch.setattr(run, "capture_file", flaky)
    dbp = tmp_path / "a.db"
    s = run_import(dbp, fixture_tree)
    assert s.status == "errors" and s.records_added == TOTAL_FIXTURE_LINES


def test_import_run_row_written(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        run_row = db.query(ImportRun).one()
        assert run_row.status == "ok" and run_row.finished_at is not None


# --- Amendments from the review rounds ----------------------------------------------------


def test_bad_file_records_error_anomaly_and_continues(tmp_path, fixture_tree, monkeypatch):
    """The contained failure leaves a persisted ``file_ingest_failure`` error anomaly whose
    detail carries the path + error, and every other file still imports (amendment 4)."""
    real_capture = run.capture_file

    def flaky(db, f):
        if f.kind == "backup":
            raise OSError("synthetic ingest failure")
        return real_capture(db, f)

    monkeypatch.setattr(run, "capture_file", flaky)
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)

    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        anomaly = db.query(ParseAnomaly).filter_by(kind="file_ingest_failure").one()
        assert anomaly.severity == "error"
        assert "path" in anomaly.detail and "error" in anomaly.detail
        # the run still captured every good line
        assert db.query(RawRecord).count() == TOTAL_FIXTURE_LINES


def test_unparsed_row_sweep_self_heals(tmp_path, fixture_tree):
    """A raw record left with a NULL provenance stamp and no derived rows (the crash window
    between capture-commit and interpret-commit) is re-interpreted by the next run's sweep
    (amendment 2)."""
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)

    engine = get_engine(dbp)
    factory = session_factory(engine)
    with factory() as db:
        # Simulate the crash: drop an ai-title's derived SessionEvent and NULL its stamp so the
        # sweep must rebuild it. (ai-title -> SessionEvent, which has no child rows to unwind.)
        raw = db.query(RawRecord).filter_by(record_type="ai-title").first()
        rid = raw.id
        db.query(SessionEvent).filter_by(raw_record_id=rid).delete()
        raw.parsed_with_schema_version = None
        db.commit()

    s = run_import(dbp, fixture_tree)
    assert s.records_swept == 1

    with factory() as db:
        healed = db.get(RawRecord, rid)
        assert healed.parsed_with_schema_version == SCHEMA_VERSION
        assert db.query(SessionEvent).filter_by(raw_record_id=rid).count() == 1


def test_sweep_isolates_a_failing_record(tmp_path, fixture_tree, monkeypatch):
    """A record whose re-interpretation raises during the sweep is contained: it gets an
    ``interpret_failure`` error anomaly + ``parse_status='anomaly'`` and the run does not abort
    (amendment 2, mirroring capture's ``_interpret_chunk`` semantics)."""
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)

    engine = get_engine(dbp)
    factory = session_factory(engine)
    with factory() as db:
        raw = db.query(RawRecord).filter_by(record_type="ai-title").first()
        rid = raw.id
        db.query(SessionEvent).filter_by(raw_record_id=rid).delete()
        raw.parsed_with_schema_version = None
        db.commit()

    def boom(db, pr, raw):
        raise RuntimeError("synthetic sweep failure")

    monkeypatch.setattr(run.interpret, "apply", boom)
    s = run_import(dbp, fixture_tree)
    assert s.records_swept == 1 and s.status == "errors"

    with factory() as db:
        healed = db.get(RawRecord, rid)
        assert healed.parse_status == "anomaly"
        assert (
            db.query(ParseAnomaly)
            .filter_by(raw_record_id=rid, kind="interpret_failure")
            .count()
            == 1
        )


def test_unhandled_exception_finalizes_run_as_fatal(tmp_path, fixture_tree, monkeypatch):
    """An unhandled exception mid-run re-raises (cron fails loudly) AND finalizes the
    ImportRun row as 'fatal' with finished_at + best-effort counts -- no zombie 'running' row
    (review fix 1)."""

    def boom(db, discovered):
        raise RuntimeError("synthetic fatal failure")

    monkeypatch.setattr(run, "detect_gone", boom)
    dbp = tmp_path / "a.db"
    with pytest.raises(RuntimeError, match="synthetic fatal failure"):
        run_import(dbp, fixture_tree)

    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        row = db.query(ImportRun).one()
        assert row.status == "fatal" and row.finished_at is not None
        # capture completed before the failure: best-effort counts were preserved
        assert row.records_added == TOTAL_FIXTURE_LINES and row.files_seen == 5


def test_sweep_skips_previously_failed_records(tmp_path, fixture_tree):
    """A NULL-stamped record with a prior interpret_failure anomaly is a deterministic
    failure under the current schema: the sweep must NOT retry it every run (it re-arms only
    via reparse, which deletes interpret_failure anomalies first) -- review fix 2."""
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)

    engine = get_engine(dbp)
    factory = session_factory(engine)
    with factory() as db:
        raw = db.query(RawRecord).filter_by(record_type="ai-title").first()
        raw.parsed_with_schema_version = None
        raw.parse_status = "anomaly"
        db.add(
            ParseAnomaly(
                raw_record_id=raw.id,
                source_file_id=raw.source_file_id,
                severity="error",
                kind="interpret_failure",
                detail={"error": "prior deterministic failure"},
                schema_version=SCHEMA_VERSION,
                created_at=utcnow(),
            )
        )
        db.commit()
        anomalies_before = db.query(ParseAnomaly).count()

    s2 = run_import(dbp, fixture_tree)
    s3 = run_import(dbp, fixture_tree)
    assert s2.records_swept == 0 and s3.records_swept == 0

    with factory() as db:
        # anomaly count stays flat across both runs: the record was never re-attempted
        assert db.query(ParseAnomaly).count() == anomalies_before
        unswept = db.query(RawRecord).filter_by(record_type="ai-title").first()
        assert unswept.parsed_with_schema_version is None  # still awaiting a schema change


def test_records_skipped_duplicate_aggregated(tmp_path, fixture_tree):
    """CaptureStats' duplicate skips are summed into the summary and persisted on the run row
    (amendment 3). The .bak file replays session 1's first two lines -> both dedup."""
    dbp = tmp_path / "a.db"
    s = run_import(dbp, fixture_tree)
    assert s.records_skipped_duplicate == 2

    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        assert db.query(ImportRun).one().records_skipped_duplicate == 2


def test_summary_shape_is_stable():
    """ImportSummary keeps the brief's mandated fields (extra fields are permitted)."""
    s = ImportSummary(
        run_id=1,
        files_seen=2,
        records_added=3,
        records_skipped_duplicate=4,
        anomaly_count=5,
        gone_flipped=6,
        status="ok",
    )
    assert s.records_swept == 0  # extra observability field defaults to 0
