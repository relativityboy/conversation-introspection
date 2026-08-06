"""Reassembly integration tests: capture consuming `read_complete_units` (compat spec §2).

These pin the higher-level guarantee that Task 2's reader exists to serve: a hand-edited,
pretty-printed transcript record lands in `raw_records` as ONE row (not N torn/anomalous
ones), while native compact records and give-up fragments still capture exactly as they did
line-by-line before this task. See `test_capture.py::test_capture_compact_rows_golden` for
the companion pin proving the compact fast path is untouched byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json

from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import Message, ParseAnomaly, RawRecord, SourceFile
from tests.fixtures.records import make_assistant_line, make_user_line

PRETTY_SESSION_UUID = "7a7a7a7a-0000-4000-8000-000000000007"


def _pretty_line(builder_bytes: bytes) -> bytes:
    return (json.dumps(json.loads(builder_bytes), indent=2, ensure_ascii=False) + "\n").encode()


def _write_and_capture(db, tmp_path, payload: bytes):
    proj = tmp_path / "pretty" / "-Users-x-pretty"
    proj.mkdir(parents=True)
    (proj / f"{PRETTY_SESSION_UUID}.jsonl").write_bytes(payload)
    for f in discover(tmp_path / "pretty"):
        capture_file(db, f)
    db.commit()


def test_pretty_head_compact_tail_captures_clean(db_session, tmp_path):
    pretty = _pretty_line(make_user_line(text="hand edited", sessionId=PRETTY_SESSION_UUID))
    compact = make_assistant_line(text="native", sessionId=PRETTY_SESSION_UUID)
    _write_and_capture(db_session, tmp_path, pretty + compact)
    recs = db_session.query(RawRecord).order_by(RawRecord.line_number).all()
    assert [r.reassembled for r in recs] == [True, False]
    assert recs[0].raw_line == pretty and recs[0].line_number == 1
    assert recs[1].line_number == pretty.count(b"\n") + 1  # file-position ordinal
    assert db_session.query(ParseAnomaly).count() == 0
    assert db_session.query(Message).count() == 2  # both interpreted


def test_incremental_append_after_pretty_head(db_session, tmp_path):
    # capture pretty head; then append a compact record and re-capture: prefix model holds
    pretty = _pretty_line(make_user_line(text="hand edited", sessionId=PRETTY_SESSION_UUID))
    _write_and_capture(db_session, tmp_path, pretty)

    path = tmp_path / "pretty" / "-Users-x-pretty" / f"{PRETTY_SESSION_UUID}.jsonl"
    compact = make_assistant_line(text="native tail", sessionId=PRETTY_SESSION_UUID)
    with path.open("ab") as fh:
        fh.write(compact)
    for f in discover(tmp_path / "pretty"):
        capture_file(db_session, f)
    db_session.commit()

    sf = db_session.query(SourceFile).filter(SourceFile.path == str(path)).one()
    assert sf.status == "active"
    # Same generation on both runs: the resumed prefix hash matched, so no divergence was
    # triggered — capture's own byte-faithful resume model (unchanged by this task) holds.
    assert sf.generation == 0
    full_bytes = path.read_bytes()
    assert sf.prefix_hash == hashlib.sha256(full_bytes).hexdigest()
    assert sf.byte_offset_checkpoint == len(full_bytes)

    recs = db_session.query(RawRecord).order_by(RawRecord.line_number).all()
    assert [r.reassembled for r in recs] == [True, False]
    assert recs[1].raw_line == compact
    assert recs[1].line_number == pretty.count(b"\n") + 1  # file-position ordinal


def test_unbalanced_giveup_matches_per_line_anomalies(db_session, tmp_path):
    # opener that never closes + fragment lines: capture must record them as
    # per-line invalid_json anomalies exactly like today (spec §2 give-up)
    opener = b"{\n"
    fragment = b'  "type": "fragment",\n'
    mismatched_closer = b"}}\n"  # extra '}' drives balance negative -> give-up, not reassembly
    payload = opener + fragment + mismatched_closer
    _write_and_capture(db_session, tmp_path, payload)

    recs = db_session.query(RawRecord).order_by(RawRecord.line_number).all()
    assert [r.reassembled for r in recs] == [False, False, False]
    assert [r.raw_line for r in recs] == [opener, fragment, mismatched_closer]
    assert [r.line_number for r in recs] == [1, 2, 3]

    anomalies = db_session.query(ParseAnomaly).filter_by(kind="invalid_json").all()
    assert len(anomalies) == 3
    assert all(a.severity == "error" for a in anomalies)
    assert {a.raw_record_id for a in anomalies} == {r.id for r in recs}
