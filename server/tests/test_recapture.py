"""Recapture tests (Task 5, compat spec §3): byte-reconciled repair of a shattered file.

Before the pretty-JSONL reassembly fix, a hand-pretty-printed record was captured one
PHYSICAL LINE at a time -- every continuation line failed to parse alone and was stored as
its own ``invalid_json``-anomalous ``RawRecord``. ``_capture_shattered`` below reproduces
that exact incident shape by driving capture's own private helpers with per-line units
(``line_span=1``) instead of the current reassembly-aware ``read_complete_units`` -- the
same "reach into capture internals" pattern test_capture.py's ``_capture_all`` already uses.

The five tests below are the binding contract (task-5-brief.md Step 1, written in full; the
comments there are their contracts).
"""

from introspect.export import export_transcript
from introspect.ingest import capture as capture_mod
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.ingest.reader import RawUnit, read_complete_lines
from introspect.ingest.recapture import recapture_file
from introspect.models import ImportRun, Message, ParseAnomaly, RawRecord, SourceFile
from introspect.search import get_search_index
from tests.fixtures.records import (
    make_assistant_line,
    make_pretty,
    make_thin_meta_line,
    make_user_line,
)
from tests.test_capture import _capture_all


def _capture_shattered(db, root):
    """Capture every file under ``root`` the PRE-TASK-3 way: each raw file line becomes its
    own unit (``line_span=1``, ``reassembled=False``), so a hand-pretty-printed record's
    continuation lines each land as their own (almost always ``invalid_json``) row -- the
    exact incident shape recapture exists to heal. Mirrors ``capture_file``'s own loop
    (see capture.py) but swaps ``read_complete_units`` for a thin per-line wrapper around
    ``read_complete_lines``, driving the same private get-or-create/resume/chunk helpers
    ``capture_file`` itself uses.
    """
    for f in discover(root):
        now = capture_mod.utcnow()
        size_at_start = f.path.stat().st_size
        project = capture_mod._get_or_create_project(db, f.project_slug, now)
        session = capture_mod._get_or_create_session(db, f.session_uuid, project)
        transcript = capture_mod._get_or_create_transcript(db, f, session)
        source_file = capture_mod._get_or_create_source_file(
            db, f, project, transcript, size_at_start, now
        )
        db.commit()
        running, file_line_number, diverged = capture_mod._resume_prefix_state(
            f.path, source_file.byte_offset_checkpoint, source_file.prefix_hash
        )
        stats = capture_mod.CaptureStats(0, 0, 0)
        chunk = [
            RawUnit(rl.data, rl.start_offset, rl.end_offset, 1, False)
            for rl in read_complete_lines(f.path, from_offset=source_file.byte_offset_checkpoint)
        ]
        if chunk:
            capture_mod._capture_chunk(
                db, project, transcript, source_file, chunk, running,
                file_line_number, size_at_start, stats, bypass_dedup=diverged,
            )


def test_recapture_heals_shattered_pretty_file(db_session, tmp_path):
    # 1. Write pretty+compact file; capture it SHATTERED via the per-line shim.
    #    Assert the incident shape: N invalid_json anomalies, 0 messages from the head.
    root = tmp_path / "r"
    slug = root / "-Users-x-pretty"
    slug.mkdir(parents=True)
    uuid = "7c7c7c7c-0000-4000-8000-000000000007"
    head_user = make_pretty(make_user_line(text="edited by hand", sessionId=uuid))
    head_meta = make_pretty(make_thin_meta_line("mode", session_id=uuid))
    tail = make_assistant_line(text="native tail", sessionId=uuid)
    content = head_user + head_meta + tail
    path = slug / f"{uuid}.jsonl"
    path.write_bytes(content)

    _capture_shattered(db_session, root)
    n_head_lines = head_user.count(b"\n") + head_meta.count(b"\n")
    assert db_session.query(ParseAnomaly).filter_by(kind="invalid_json").count() == n_head_lines
    # Only the native tail line parses on its own -- nothing from the shattered head does.
    assert db_session.query(Message).count() == 1

    # 2. recapture_file(db, uuid) with dry_run=True: stats show the would-be swap,
    #    DB unchanged (same anomaly count, same record ids).
    records_before = db_session.query(RawRecord).count()
    anomalies_before = db_session.query(ParseAnomaly).count()
    ids_before = sorted(rid for (rid,) in db_session.query(RawRecord.id).all())

    dry = recapture_file(db_session, uuid, dry_run=True)
    assert dry.reconciled is True
    assert dry.records_before == records_before
    assert dry.records_after == 3  # pretty user unit + pretty meta unit + native tail unit

    assert db_session.query(RawRecord).count() == records_before
    assert db_session.query(ParseAnomaly).count() == anomalies_before
    assert sorted(rid for (rid,) in db_session.query(RawRecord.id).all()) == ids_before
    assert db_session.query(ImportRun).count() == 0

    # 3. recapture_file(db, uuid): reconciled True; head records now reassembled=True;
    #    anomalies (invalid_json for this file) == 0; messages/blocks exist; FTS finds
    #    a term from the pretty head (search_index query); export still byte-identical.
    result = recapture_file(db_session, uuid)
    assert result.reconciled is True
    assert result.records_after == 3

    head_rows = (
        db_session.query(RawRecord)
        .join(SourceFile)
        .filter(SourceFile.path == str(path))
        .order_by(RawRecord.line_number)
        .all()
    )
    assert len(head_rows) == 3
    assert head_rows[0].reassembled is True  # pretty user record
    assert head_rows[1].reassembled is True  # pretty thin-meta record
    assert head_rows[2].reassembled is False  # native compact tail

    sf_id = head_rows[0].source_file_id
    assert (
        db_session.query(ParseAnomaly).filter_by(source_file_id=sf_id, kind="invalid_json").count()
        == 0
    )
    # The reassembled user record + the native assistant tail both interpret to a Message;
    # the thin-meta record is a SessionEvent, not a Message.
    assert db_session.query(Message).count() == 2

    hits, total = get_search_index().search(db_session, "edited", session_uuid=uuid)
    assert total >= 1

    assert export_transcript(db_session, uuid) == content


def test_recapture_refuses_on_byte_mismatch(db_session, tmp_path):
    # Capture file normally; then corrupt ONE stored raw_line in the DB (simulate drift);
    # recapture must refuse: reconciled False, non-zero-style outcome, NOTHING mutated
    # (record ids, anomaly counts, checkpoints all unchanged).
    root = tmp_path / "r"
    slug = root / "-Users-x-drift"
    slug.mkdir(parents=True)
    uuid = "8d8d8d8d-0000-4000-8000-000000000008"
    content = make_user_line(text="one", sessionId=uuid) + make_assistant_line(
        text="two", sessionId=uuid
    )
    path = slug / f"{uuid}.jsonl"
    path.write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    db_session.commit()

    drifted = (
        db_session.query(RawRecord)
        .join(SourceFile)
        .filter(SourceFile.path == str(path))
        .order_by(RawRecord.line_number)
        .first()
    )
    drifted.raw_line = drifted.raw_line.replace(b"one", b"ONE-DRIFTED-IN-THE-DB")
    db_session.commit()

    ids_before = sorted(rid for (rid,) in db_session.query(RawRecord.id).all())
    anomalies_before = db_session.query(ParseAnomaly).count()
    checkpoint_before = (
        db_session.query(SourceFile.byte_offset_checkpoint).filter(SourceFile.path == str(path)).scalar()
    )

    result = recapture_file(db_session, uuid)

    assert result.reconciled is False
    assert sorted(rid for (rid,) in db_session.query(RawRecord.id).all()) == ids_before
    assert db_session.query(ParseAnomaly).count() == anomalies_before
    checkpoint_after = (
        db_session.query(SourceFile.byte_offset_checkpoint).filter(SourceFile.path == str(path)).scalar()
    )
    assert checkpoint_after == checkpoint_before
    assert db_session.query(ImportRun).count() == 0  # a refused run is a no-op, real or dry


def test_recapture_is_idempotent(db_session, tmp_path):
    # Healthy reassembled file → recapture is a reconciled no-op: same record count,
    # same shas, anomaly count unchanged; run twice.
    root = tmp_path / "r"
    slug = root / "-Users-x-idem"
    slug.mkdir(parents=True)
    uuid = "9e9e9e9e-1111-4000-8000-000000000009"
    content = make_pretty(make_user_line(text="already healthy", sessionId=uuid)) + make_assistant_line(
        text="native tail", sessionId=uuid
    )
    path = slug / f"{uuid}.jsonl"
    path.write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)  # Task 3's capture already reassembles correctly
    db_session.commit()

    def snapshot():
        rows = (
            db_session.query(RawRecord.line_number, RawRecord.line_sha256)
            .join(SourceFile)
            .filter(SourceFile.path == str(path))
            .order_by(RawRecord.line_number)
            .all()
        )
        return rows, db_session.query(ParseAnomaly).count()

    before = snapshot()
    r1 = recapture_file(db_session, uuid)
    after1 = snapshot()
    r2 = recapture_file(db_session, uuid)
    after2 = snapshot()

    assert r1.reconciled is True and r2.reconciled is True
    assert before == after1 == after2
    assert export_transcript(db_session, uuid) == content


def test_recapture_preserves_capture_phase_bookkeeping(db_session, fixture_tree):
    # Force a source_diverged anomaly (rewrite line 1 + re-capture, the
    # test_export_roundtrip.py:72-79 recipe), then recapture the new generation's file:
    # the source_diverged row must survive.
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-recap1"}\n'
        + content[content.index(b"\n") + 1 :]
    )
    _capture_all(db_session, fixture_tree)  # divergence: freezes the old generation
    before = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()

    result = recapture_file(db_session, main.session_uuid)
    assert result.reconciled is True

    after = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()
    assert after.id == before.id
    assert after.detail == before.detail


def test_recapture_records_run_row(db_session, tmp_path):
    # After a heal: newest ImportRun has trigger='recapture', status 'ok',
    # records_added == records_after, anomaly_count reflecting the file's new state.
    root = tmp_path / "r"
    slug = root / "-Users-x-run"
    slug.mkdir(parents=True)
    uuid = "a0a0a0a0-0000-4000-8000-00000000000a"
    content = make_pretty(make_user_line(text="run row heal", sessionId=uuid)) + make_assistant_line(
        text="native tail", sessionId=uuid
    )
    (slug / f"{uuid}.jsonl").write_bytes(content)
    _capture_shattered(db_session, root)

    result = recapture_file(db_session, uuid)
    assert result.reconciled is True

    run = db_session.query(ImportRun).order_by(ImportRun.id.desc()).first()
    assert run is not None
    assert run.trigger == "recapture"
    assert run.status == "ok"
    assert run.records_added == result.records_after
    assert run.anomaly_count == 0  # clean heal: this run introduced no new anomalies
