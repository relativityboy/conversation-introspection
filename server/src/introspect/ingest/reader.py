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

import os
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
