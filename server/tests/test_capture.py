"""Capture pipeline tests: fresh ingest, idempotent rerun, tail append, torn writes.

Capture is the archive-critical core: raw bytes must land in SQLite byte-faithfully and
crash-safely. The first five tests are the binding contract (verbatim from task-6-brief);
the remainder pin the dedup count, whitespace grading, is_primary rule, subagent metadata,
and the envelope-cwd backfill that the brief also specifies.
"""

import hashlib
import json

from introspect.ingest import interpret
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import ParseAnomaly, Project, RawRecord, SourceFile, Transcript
from introspect.schema import SCHEMA_VERSION
from tests.conftest import (
    AGENT_TOOL_USE_ID,
    AGENT_TYPE,
    PROJECT_SLUG_1,
    PROJECT_SLUG_2,
    SESSION_UUID_1,
    SESSION_UUID_2,
    TOTAL_FIXTURE_LINES,
)
from tests.fixtures.records import DEFAULT_CWD, make_user_line


def _capture_all(db, root):
    return {f.path: capture_file(db, f) for f in discover(root)}


# --- Binding contract (verbatim from task-6-brief) --------------------------------------


def test_fresh_ingest_counts(db_session, fixture_tree):
    stats = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats.values()) == TOTAL_FIXTURE_LINES
    assert db_session.query(RawRecord).count() == TOTAL_FIXTURE_LINES


def test_rerun_is_noop(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    stats2 = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats2.values()) == 0


def test_tail_append_ingests_only_new(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(make_user_line(text="post-checkpoint prompt"))
    stats2 = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats2.values()) == 1


def test_partial_trailing_line_not_captured_then_captured(db_session, fixture_tree):
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(b'{"type":"user"')          # torn write, no newline
    _capture_all(db_session, fixture_tree)
    n1 = db_session.query(RawRecord).count()
    with main.path.open("ab") as fh:
        fh.write(b',"message":{"role":"user","content":"x"}}\n')
    _capture_all(db_session, fixture_tree)
    assert db_session.query(RawRecord).count() == n1 + 1


def test_raw_bytes_stored_exactly(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    src = main.path.read_bytes()
    rows = (db_session.query(RawRecord).join(SourceFile)
            .filter(SourceFile.path == str(main.path))
            .order_by(RawRecord.line_number).all())
    assert b"".join(r.raw_line for r in rows) == src


def test_capture_compact_rows_golden(db_session, fixture_tree):
    """Fast-path conservation pin (compat spec §2): switching capture to unit
    iteration must not move a single byte, boundary, or hash for compact files."""
    _capture_all(db_session, fixture_tree)
    rows = (
        db_session.query(
            RawRecord.line_number, RawRecord.byte_offset,
            RawRecord.line_sha256, RawRecord.reassembled,
        )
        .join(SourceFile)
        .filter(SourceFile.path.like(f"%{SESSION_UUID_1}.jsonl"))
        .order_by(RawRecord.line_number)
        .all()
    )
    assert all(r.reassembled is False for r in rows)
    assert [r.line_number for r in rows] == list(range(1, len(rows) + 1))
    # byte_offset of row N+1 == byte_offset of row N + len(raw_line N): arithmetic pin
    raws = [r for (r,) in db_session.query(RawRecord.raw_line).join(SourceFile)
            .filter(SourceFile.path.like(f"%{SESSION_UUID_1}.jsonl"))
            .order_by(RawRecord.line_number)]
    offs = [r.byte_offset for r in rows]
    assert offs == [sum(len(x) for x in raws[:i]) for i in range(len(raws))]


# --- Supplementary coverage of the remaining binding semantics --------------------------


def test_backup_lines_skipped_as_duplicates(db_session, fixture_tree):
    """The .bak file duplicates its main's first 2 (uuid-bearing) lines -> both dedup."""
    stats = _capture_all(db_session, fixture_tree)
    assert sum(s.records_skipped_duplicate for s in stats.values()) == 2
    # The two skipped lines are never stored, so the transcript holds only the main file's
    # records; nothing is double-counted.
    assert db_session.query(RawRecord).count() == TOTAL_FIXTURE_LINES


def test_is_primary_rule(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    by_kind = {sf.kind: sf.is_primary for sf in db_session.query(SourceFile).all()}
    assert by_kind["main"] is True
    assert by_kind["subagent"] is True
    assert by_kind["backup"] is False


def test_subagent_transcript_carries_agent_meta(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    sub = db_session.query(Transcript).filter_by(kind="subagent").one()
    assert sub.agent_hex_id == "abc123"
    assert sub.agent_type == AGENT_TYPE
    assert sub.agent_description
    assert sub.parent_tool_use_id == AGENT_TOOL_USE_ID


def test_envelope_cwd_backfills_project(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    proj = db_session.query(Project).filter_by(dir_slug=PROJECT_SLUG_1).one()
    assert proj.resolved_cwd == DEFAULT_CWD


def test_whitespace_line_captured_as_info_not_error(db_session, fixture_tree):
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(b"   \n")  # torn-write residue: whitespace, complete line
    _capture_all(db_session, fixture_tree)
    ws = (db_session.query(RawRecord).join(SourceFile)
          .filter(SourceFile.path == str(main.path), RawRecord.raw_line == b"   \n").one())
    assert ws.parse_status == "partial"
    anomaly = db_session.query(ParseAnomaly).filter_by(raw_record_id=ws.id).one()
    assert anomaly.severity == "info"
    assert anomaly.kind == "whitespace_line"


def test_whitespace_line_stamped_with_schema_version(db_session, fixture_tree):
    """A whitespace-only line is graded, not skipped: its provenance stamp must be non-NULL
    so the future NULL-stamp sweep never re-attempts it."""
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(b"   \n")
    _capture_all(db_session, fixture_tree)
    ws = (db_session.query(RawRecord).join(SourceFile)
          .filter(SourceFile.path == str(main.path), RawRecord.raw_line == b"   \n").one())
    assert ws.parsed_with_schema_version == SCHEMA_VERSION


def test_prefix_hash_and_checkpoint_survive_resume(db_session, fixture_tree):
    """prefix_hash is sha256(file[0:checkpoint]) and is rebuilt correctly on tail-resume."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")

    sf1 = db_session.query(SourceFile).filter_by(path=str(main.path)).one()
    assert sf1.byte_offset_checkpoint == main.path.stat().st_size
    assert sf1.prefix_hash == hashlib.sha256(main.path.read_bytes()).hexdigest()

    with main.path.open("ab") as fh:
        fh.write(make_user_line(text="a second run appends this"))
    _capture_all(db_session, fixture_tree)  # resume: prefix re-hashed by streaming the file

    db_session.expire_all()
    sf2 = db_session.query(SourceFile).filter_by(path=str(main.path)).one()
    assert sf2.byte_offset_checkpoint == main.path.stat().st_size
    assert sf2.prefix_hash == hashlib.sha256(main.path.read_bytes()).hexdigest()


def test_fully_deduped_backup_prefix_hash_is_file_hash(db_session, fixture_tree):
    """A backup whose every line dedups still hashes/checkpoints its REAL file bytes.

    Task 7's divergence check compares sha256(file[0:checkpoint]) against prefix_hash, so a
    fully-skipped file must carry the hash of its actual prefix, not the empty-string hash.
    """
    _capture_all(db_session, fixture_tree)
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    sf = db_session.query(SourceFile).filter_by(path=str(bak.path)).one()
    assert sf.byte_offset_checkpoint == bak.path.stat().st_size
    assert sf.prefix_hash == hashlib.sha256(bak.path.read_bytes()).hexdigest()
    stored = (db_session.query(RawRecord).join(SourceFile)
              .filter(SourceFile.path == str(bak.path)).count())
    assert stored == 0


def test_backup_with_uuidless_line_fully_dedups(db_session, fixture_tree):
    """Backup = main's first 3 lines incl. a uuid-less ai-title: all 3 skip, 0 stored.

    line_number is the file-position ordinal, so the uuid-less dedup key
    (transcript, line_sha256, line_number) means the same position in both files.
    """
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    first3 = b"".join(main.path.read_bytes().splitlines(keepends=True)[:3])
    bak.path.write_bytes(first3)  # lines: user(uuid), assistant(uuid), ai-title(no uuid)

    stats = _capture_all(db_session, fixture_tree)
    assert stats[bak.path].records_added == 0
    assert stats[bak.path].records_skipped_duplicate == 3
    stored = (db_session.query(RawRecord).join(SourceFile)
              .filter(SourceFile.path == str(bak.path)).count())
    assert stored == 0


def test_uuid_content_conflict_detected(db_session, fixture_tree):
    """A record_uuid reappearing with different bytes is captured AND flagged as an error."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    first = json.loads(main.path.read_bytes().splitlines()[0])
    first["message"]["content"] = "history rewritten under the same uuid"
    mutated = (json.dumps(first, separators=(",", ":")) + "\n").encode()
    with main.path.open("ab") as fh:
        fh.write(mutated)

    stats2 = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats2.values()) == 1  # conflict line IS captured
    anomaly = db_session.query(ParseAnomaly).filter_by(kind="uuid_content_conflict").one()
    assert anomaly.severity == "error"
    assert anomaly.detail["uuid"] == first["uuid"]
    assert anomaly.detail["existing_sha"] != anomaly.detail["incoming_sha"]
    rec = db_session.query(RawRecord).filter_by(id=anomaly.raw_record_id).one()
    assert rec.raw_line == mutated


def test_second_copy_at_second_path_is_not_primary(db_session, fixture_tree):
    """A restored copy of a session at another path must not become a second primary."""
    src = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_2}.jsonl"
    dst = fixture_tree / PROJECT_SLUG_2 / f"{SESSION_UUID_2}.jsonl"
    dst.write_bytes(src.read_bytes())

    _capture_all(db_session, fixture_tree)
    prims = {sf.path: sf.is_primary
             for sf in db_session.query(SourceFile)
             .filter(SourceFile.path.in_([str(src), str(dst)]))}
    assert prims[str(src)] is True
    assert prims[str(dst)] is False


def test_same_file_byte_identical_uuid_line_is_stored(db_session, fixture_tree):
    """A byte-identical repeat of a uuid-bearing line in the SAME file must be stored.

    Dedup exists to skip other COPIES of a transcript (backups, restored files at other
    paths) — it must never drop bytes from the file itself, or export stops being
    byte-identical and the loss repeats forever (the line re-skips on every future run).
    """
    stats1 = _capture_all(db_session, fixture_tree)
    # Baseline: dedup from a DIFFERENT path (the .bak) still skips.
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    assert stats1[bak.path].records_skipped_duplicate == 2

    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    first_line = main.path.read_bytes().splitlines(keepends=True)[0]  # uuid-bearing user line
    with main.path.open("ab") as fh:
        fh.write(first_line)

    stats2 = _capture_all(db_session, fixture_tree)
    assert stats2[main.path].records_added == 1  # stored, NOT skipped as a duplicate

    uuid = json.loads(first_line)["uuid"]
    rows = (db_session.query(RawRecord).join(SourceFile)
            .filter(SourceFile.path == str(main.path))
            .order_by(RawRecord.line_number).all())
    assert [r.record_uuid for r in rows].count(uuid) == 2  # same uuid, both records kept
    assert b"".join(r.raw_line for r in rows) == main.path.read_bytes()  # export == file
    # A same-file identical repeat is not a content conflict either.
    assert (db_session.query(ParseAnomaly)
            .filter_by(kind="uuid_content_conflict").count()) == 0


def test_interpret_failure_never_rolls_back_capture(db_session, fixture_tree, monkeypatch):
    """Capture is sacred: an exception in interpret.apply must not lose a captured line."""

    def boom(db, pr, raw):
        raise RuntimeError("synthetic interpret failure")

    monkeypatch.setattr(interpret, "apply", boom)
    _capture_all(db_session, fixture_tree)

    # Every fixture line is still captured despite interpretation blowing up on each one.
    assert db_session.query(RawRecord).count() == TOTAL_FIXTURE_LINES
    # ...and each failure is recorded as an interpret_failure anomaly + parse_status flip.
    failures = db_session.query(ParseAnomaly).filter_by(kind="interpret_failure").count()
    assert failures == TOTAL_FIXTURE_LINES
    assert db_session.query(RawRecord).filter_by(parse_status="anomaly").count() == (
        TOTAL_FIXTURE_LINES
    )
