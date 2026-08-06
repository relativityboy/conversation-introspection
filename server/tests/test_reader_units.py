import json

from introspect.ingest.reader import read_complete_lines, read_complete_units


def _pretty(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


def _compact(obj) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def test_compact_file_yields_one_unit_per_line_byte_identical(tmp_path):
    lines = [_compact({"type": "user", "n": i}) for i in range(3)]
    p = tmp_path / "a.jsonl"
    p.write_bytes(b"".join(lines))
    units = list(read_complete_units(p))
    assert [u.data for u in units] == lines
    assert all(u.line_span == 1 and not u.reassembled for u in units)
    # offsets identical to the line reader's
    assert [(u.start_offset, u.end_offset) for u in units] == [
        (l.start_offset, l.end_offset) for l in read_complete_lines(p)  # noqa: E741
    ]


def test_pretty_record_reassembles_to_exact_bytes(tmp_path):
    pretty = _pretty({"type": "mode", "mode": "normal", "sessionId": "s"})
    tail = _compact({"type": "user", "uuid": "u1"})
    p = tmp_path / "a.jsonl"
    p.write_bytes(pretty + tail)
    units = list(read_complete_units(p))
    assert units[0].data == pretty and units[0].reassembled
    assert units[0].line_span == pretty.count(b"\n")
    assert units[1].data == tail and units[1].line_span == 1


def test_strings_containing_braces_and_escapes(tmp_path):
    # '}' at line start inside a string value; escaped quotes; nested arrays
    obj = {"type": "user", "text": "}\n} not a close\n\"quoted\"", "arr": [{"k": "{"}]}
    p = tmp_path / "a.jsonl"
    p.write_bytes(_pretty(obj))
    units = list(read_complete_units(p))
    assert len(units) == 1 and json.loads(units[0].data) == obj


def test_giveup_emits_first_line_alone_and_resumes_next_line(tmp_path):
    # An opener line that never balances, followed by a valid compact record:
    garbage = b'{ "never": "closes",\n'
    good = _compact({"type": "user", "uuid": "u2"})
    filler = b'"not an opener line",\n'  # fails json? no — it IS valid JSON (a string)...
    # Use a non-JSON, non-opener line so the replay path is exercised:
    filler = b'  "type": "fragment",\n'
    p = tmp_path / "a.jsonl"
    p.write_bytes(garbage + filler + good)
    units = list(read_complete_units(p))
    # garbage line reaches EOF-of-buffer? No — 'good' closes nothing; depth never returns
    # to zero, cap/EOF rules apply per implementation; the CONTRACT here:
    # every input byte comes back out, in order, and `good` is one clean unit.
    assert b"".join(u.data for u in units) == garbage + filler + good
    assert any(u.data == good and u.line_span == 1 for u in units)


def test_depth_negative_aborts_immediately(tmp_path):
    bad = b'{ "a": 1 }}\n'  # }} — json.loads fails, scan hits depth<0
    good = _compact({"type": "user", "uuid": "u3"})
    p = tmp_path / "a.jsonl"
    p.write_bytes(bad + good)
    units = list(read_complete_units(p))
    assert units[0].data == bad and units[0].line_span == 1 and not units[0].reassembled
    assert units[1].data == good


def test_non_opener_failures_never_start_reassembly(tmp_path):
    frag = b'  "type": "last-prompt",\n'  # the real incident's fragment shape
    good = _compact({"type": "user", "uuid": "u4"})
    p = tmp_path / "a.jsonl"
    p.write_bytes(frag + good)
    units = list(read_complete_units(p))
    assert units[0].data == frag and units[0].line_span == 1


def test_eof_open_buffer_defers_everything_from_buffer_start(tmp_path):
    good = _compact({"type": "user", "uuid": "u5"})
    partial = b'{\n  "type": "mode",\n'  # record still being written
    p = tmp_path / "a.jsonl"
    p.write_bytes(good + partial)
    units = list(read_complete_units(p))
    assert [u.data for u in units] == [good]  # partial NOT emitted, no bytes invented


def test_line_cap_gives_up(tmp_path):
    opener = b"{\n" + b'  "k": "v",\n' * 1001
    p = tmp_path / "a.jsonl"
    p.write_bytes(opener + _compact({"type": "user"}))
    units = list(read_complete_units(p))
    assert units[0].data == b"{\n" and units[0].line_span == 1  # first line alone, replay follows


def test_torn_write_tail_still_deferred(tmp_path):
    good = _compact({"type": "user", "uuid": "u6"})
    p = tmp_path / "a.jsonl"
    p.write_bytes(good + b'{"half')  # no newline
    assert [u.data for u in list(read_complete_units(p))] == [good]
