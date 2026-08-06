"""Byte-offset streaming line reader for append-only .jsonl transcript files.

Ingestion needs to resume tailing a growing file across process restarts, so every
line is handed back with the exact byte offsets it occupied at read time. Offsets are
computed arithmetically (``start + len(data)``), never via ``file.tell()`` mid-iteration,
because a buffered reader's file position does not track the logical line boundary once
internal read-ahead has occurred.

A file being appended to concurrently may end mid-line. That trailing, newline-less
chunk is only a real "final line" if it was already at end-of-file when we opened the
file (checked once via ``os.fstat`` at open time) — otherwise it is a partial write in
progress and must not be yielded, so the caller can retry later once the writer catches
up.
"""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RawLine:
    data: bytes  # exact bytes INCLUDING trailing \n when present
    start_offset: int
    end_offset: int  # start of next line / new checkpoint candidate


def read_complete_lines(path: Path, from_offset: int = 0) -> Iterator[RawLine]:
    """Yields complete lines only. A trailing chunk without \\n is NOT yielded
    unless it is at true EOF and the file ends without a newline — then it IS
    yielded (final-line-no-newline case). Distinguish via: a line missing \\n
    is yielded only when file size == its end_offset at open time."""
    with path.open("rb") as f:
        size_at_open = os.fstat(f.fileno()).st_size
        f.seek(from_offset)
        offset = from_offset
        while True:
            data = f.readline()
            if not data:
                return
            end_offset = offset + len(data)
            if not data.endswith(b"\n") and end_offset != size_at_open:
                return
            yield RawLine(data=data, start_offset=offset, end_offset=end_offset)
            offset = end_offset


_REASSEMBLY_MAX_LINES = 1000
_REASSEMBLY_MAX_BYTES = 1024 * 1024


@dataclass
class RawUnit:
    """One logical record's worth of consumed bytes (spec §2). For a native (compact,
    single-line) record this is exactly one `RawLine`'s worth; for a reassembled
    pretty-printed record it is the exact concatenation of every line that made it up,
    including their internal newlines — the reassembly moves record boundaries, it
    never invents, drops, or otherwise touches a byte."""

    data: bytes  # exact consumed bytes: one line, or N whole lines incl. internal \n
    start_offset: int
    end_offset: int
    line_span: int  # file lines consumed (1 for native records)
    reassembled: bool  # True iff line_span > 1


def _is_single_json(data: bytes) -> bool:
    """True iff `data` parses standalone as one complete JSON document — spec §2's
    fast-path test: a line that already parses on its own needs no reassembly."""
    try:
        json.loads(data)
        return True
    except (ValueError, TypeError):
        return False


def _looks_like_opener(data: bytes) -> bool:
    """True iff the first non-whitespace byte is `{` — spec §2's heuristic for
    deciding whether a JSON-parse failure is worth attempting reassembly for, versus
    a genuinely malformed / non-JSON line that should just be surfaced as-is."""
    return data.lstrip().startswith(b"{")


def _scan_balance(
    data: bytes, depth: int, in_string: bool, escaped: bool
) -> tuple[int, bool, bool, bool]:
    """String-and-escape-aware brace/bracket depth scan over one chunk (spec §2:
    balance tracking must not be fooled by braces or brackets that appear inside a
    JSON string value). Depth and string state carry across chunks, so a caller can
    resume the scan line-by-line as a reassembly buffer grows. Returns
    ``(depth, in_string, escaped, went_negative)``."""
    went_negative = False
    for byte in data:
        if escaped:
            escaped = False
        elif in_string:
            if byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:  # quote
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
            if depth < 0:
                went_negative = True
    return depth, in_string, escaped, went_negative


def read_complete_units(path: Path, from_offset: int = 0) -> Iterator[RawUnit]:
    """Reassembles pretty-printed (multi-line) JSON records back into single logical
    units on top of `read_complete_lines` (spec §2).

    A line that already parses as JSON on its own is yielded immediately — the fast
    path, byte-identical to `read_complete_lines`. A line that fails to parse and
    looks like a JSON object opener (`_looks_like_opener`) starts a bounded
    reassembly attempt: subsequent lines are appended and brace/bracket-balance
    scanned (`_scan_balance`) until the balance returns to zero and the accumulated
    bytes parse as one JSON document.

    Reassembly gives up — yielding only the buffer's first line, byte-identical and
    unreassembled, then replaying the remaining accumulated lines as fresh starts —
    when: the balance goes negative, the line/byte caps are exceeded, the
    accumulated bytes are balanced but still not valid JSON, or the next candidate
    line is itself an unindented, independently-valid JSON line (a native record's
    signature; spec §2 forbids ever mis-joining two independently-valid records into
    one). Reaching EOF with a reassembly still open defers the whole buffer — yields
    nothing from it — mirroring `read_complete_lines`' own deferral of a torn,
    newline-less trailing write.
    """
    pending: deque[RawLine] = deque()
    lines = read_complete_lines(path, from_offset=from_offset)

    def next_line() -> RawLine | None:
        if pending:
            return pending.popleft()
        return next(lines, None)

    while True:
        line = next_line()
        if line is None:
            return
        if _is_single_json(line.data):
            yield RawUnit(line.data, line.start_offset, line.end_offset, 1, False)
            continue
        if not line.data.endswith(b"\n"):
            return  # torn tail: same deferral read_complete_lines uses for a live tail
        if not _looks_like_opener(line.data):
            yield RawUnit(line.data, line.start_offset, line.end_offset, 1, False)
            continue

        # Reassembly attempt: accumulate lines until balance returns to zero.
        buf = [line]
        depth, in_str, esc, neg = _scan_balance(line.data, 0, False, False)
        total = len(line.data)
        giveup = neg or depth == 0  # depth==0 here: balanced-but-invalid single line

        while not giveup:
            nxt = next_line()
            if nxt is None:
                return  # EOF with an open buffer defers the whole attempt
            if nxt.data.startswith(b"{") and _is_single_json(nxt.data):
                # Unindented and independently valid on its own: the signature of a
                # native record, never a pretty-printed continuation line (those are
                # always indented). Don't swallow it — give up and replay it fresh.
                pending.appendleft(nxt)
                giveup = True
                break
            buf.append(nxt)
            total += len(nxt.data)
            depth, in_str, esc, neg = _scan_balance(nxt.data, depth, in_str, esc)
            if neg or len(buf) > _REASSEMBLY_MAX_LINES or total > _REASSEMBLY_MAX_BYTES:
                giveup = True
            elif depth == 0:
                joined = b"".join(part.data for part in buf)
                if _is_single_json(joined):
                    yield RawUnit(joined, buf[0].start_offset, buf[-1].end_offset, len(buf), True)
                    buf = []
                    break
                giveup = True  # balanced but still not valid JSON

        if buf:
            first, *rest = buf
            pending.extendleft(reversed(rest))  # replay the remainder as fresh starts
            yield RawUnit(first.data, first.start_offset, first.end_offset, 1, False)
