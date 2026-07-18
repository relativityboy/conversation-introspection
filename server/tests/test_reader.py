from introspect.ingest.reader import read_complete_lines


def test_reads_lines_with_offsets(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}\n')
    lines = list(read_complete_lines(p))
    assert [l.data for l in lines] == [b'{"a":1}\n', b'{"b":2}\n']  # noqa: E741
    assert lines[1].start_offset == 8 and lines[1].end_offset == 16


def test_resumes_from_offset(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}\n')
    assert [l.data for l in read_complete_lines(p, from_offset=8)] == [b'{"b":2}\n']  # noqa: E741


def test_final_line_without_newline_is_yielded(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}')
    assert [l.data for l in read_complete_lines(p)][-1] == b'{"b":2}'  # noqa: E741


def test_streams_large_line(tmp_path):
    p = tmp_path / "f.jsonl"
    big = b'{"x":"' + b"y" * 600_000 + b'"}\n'
    p.write_bytes(big)
    (line,) = list(read_complete_lines(p))
    assert line.end_offset == len(big)
