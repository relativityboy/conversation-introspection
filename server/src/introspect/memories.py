"""Claude Code auto-memory scanner: pure disk reads, no FastAPI, no DB.

Memory files live at ``<source_root>/<dir_slug>/memory/*.md`` -- the same per-project
directory layout ``ingest/discovery.py`` walks for transcripts, one level down. This module
is the first thing in the codebase that reads that ``memory/`` subdirectory; it never writes,
captures, or indexes anything.

Exclusion is enforced HERE, not upstream: unlike every other read path (which serves rows
already filtered out of the archive at capture time by ``introspect.exclusion``), the
memories route reads disk directly on every request, so it is the only API read path that
lists directories and reads file content off disk besides ``ingest/discovery.py`` itself, and
it must skip an excluded project's directory before touching anything beneath it (spec
2026-08-17 §2 zero-read rule). See ``scan_memories``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_MEMORY_INDEX_FILENAME = "MEMORY.md"


@dataclass(frozen=True)
class MemoryFile:
    name: str
    description: str | None
    type: str | None
    filename: str
    path: str
    size: int
    mtime: datetime | None
    body: str | None
    error: str | None


@dataclass(frozen=True)
class MemoryProject:
    dir_slug: str
    memories: tuple[MemoryFile, ...]


@dataclass(frozen=True)
class ParsedMemoryText:
    name: str
    description: str | None
    type: str | None
    body: str
    error: str | None


def _strip_leading_blank_lines(text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    return "".join(lines[i:])


def _fm_value(raw: str) -> str:
    """Frontmatter value cleanup: strip whitespace, then ONE matching pair of quotes.

    Not real YAML, but real memory files do use YAML's escape conventions inside quoted
    scalars, so we honor the two that matter: ``\\"``/``\\\\`` inside double quotes, and the
    doubled ``''`` single-quote escape inside single quotes.
    """
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace('\\"', '"').replace("\\\\", "\\")
        else:
            value = value.replace("''", "'")
    return value


def _is_fence(line: str) -> bool:
    return line.rstrip() == "---"


def _fm_value_or_none(raw: str) -> str | None:
    """Like ``_fm_value``, but an empty value (``name:``, ``description: ""``) is absent, not
    the empty string -- callers that want a fallback (``name`` falls back to the filename stem)
    handle that themselves."""
    value = _fm_value(raw)
    return value if value else None


def parse_memory_text(text: str, filename: str) -> ParsedMemoryText:
    """Parse a memory file's text: optional YAML-ish frontmatter, then a markdown body.

    Deliberately not a real YAML parser (there is no YAML dependency in this project) --
    it recognizes exactly the handful of keys real memory files use: top-level ``name:``,
    ``description:``, ``type:``, and the newer ``metadata:`` block whose indented ``type:``
    line is the other generation's spelling of the same field. Every other key is ignored.
    """
    stem = Path(filename).stem
    text = text.lstrip("﻿")  # NOTE(claude): finding 5 -- a leading UTF-8 BOM would
    # otherwise make lines[0] read as "﻿---" and never match the fence check.
    lines = text.splitlines(keepends=True)

    if not lines or not _is_fence(lines[0]):
        description = None
        for line in lines:
            content = line.strip()
            if content:
                description = content.lstrip("#").strip()
                break
        return ParsedMemoryText(name=stem, description=description, type=None, body=text, error=None)

    close_idx = None
    for i in range(1, len(lines)):
        if _is_fence(lines[i]):
            close_idx = i
            break

    if close_idx is None:
        return ParsedMemoryText(
            name=stem, description=None, type=None, body=text, error="frontmatter unterminated"
        )

    body = _strip_leading_blank_lines("".join(lines[close_idx + 1 :]))

    name: str | None = None
    description: str | None = None
    mem_type: str | None = None
    in_metadata = False
    for raw_line in lines[1:close_idx]:
        content = raw_line.rstrip("\r\n")
        if not content.strip():
            continue  # blank line inside frontmatter: ignored, doesn't end a metadata block
        if content[0] not in (" ", "\t"):
            in_metadata = False
            if content.startswith("name:"):
                name = _fm_value_or_none(content[len("name:") :])
            elif content.startswith("description:"):
                description = _fm_value_or_none(content[len("description:") :])
            elif content.startswith("type:"):
                mem_type = _fm_value_or_none(content[len("type:") :])
            elif content.startswith("metadata:"):
                in_metadata = True
            # every other top-level key is ignored
        elif in_metadata:
            inner = content.strip()
            if inner.startswith("type:"):
                mem_type = _fm_value_or_none(inner[len("type:") :])
            # every other nested key is ignored

    return ParsedMemoryText(
        name=name if name is not None else stem,
        description=description,
        type=mem_type,
        body=body,
        error=None,
    )


def _read_memory_file(path: Path) -> MemoryFile:
    stem = path.stem
    abs_path = str(path.resolve())
    size = 0
    mtime: datetime | None = None

    try:
        st = path.stat()
    except OSError as exc:
        return MemoryFile(
            name=stem,
            description=None,
            type=None,
            filename=path.name,
            path=abs_path,
            size=size,
            mtime=mtime,
            body=None,
            error=type(exc).__name__,
        )
    size = st.st_size
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return MemoryFile(
            name=stem,
            description=None,
            type=None,
            filename=path.name,
            path=abs_path,
            size=size,
            mtime=mtime,
            body=None,
            error=type(exc).__name__,
        )

    decode_error: str | None = None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        decode_error = "UnicodeDecodeError"

    parsed = parse_memory_text(text, path.name)
    return MemoryFile(
        name=parsed.name,
        description=parsed.description,
        type=parsed.type,
        filename=path.name,
        path=abs_path,
        size=size,
        mtime=mtime,
        body=parsed.body,
        # NOTE(claude): decode_error wins over parsed.error ("frontmatter unterminated") when
        # both are present -- a file that failed to decode as UTF-8 got its frontmatter parsed
        # out of already-mangled (errors="replace") text, so the decode failure is the more
        # fundamental problem and the one worth surfacing.
        error=decode_error if decode_error is not None else parsed.error,
    )


def scan_memories(root: Path, *, excluded: frozenset[str] = frozenset()) -> list[MemoryProject]:
    """Scan every non-excluded project directory under ``root`` for listable memory files.

    A project with no ``memory/`` directory, an empty one, or one containing only the
    per-project ``MEMORY.md`` index is omitted entirely -- there is nothing to show. No file
    is ever dropped for being odd (permission errors, bad encoding, broken frontmatter): it
    is still listed, with ``error`` set.
    """
    if not root.is_dir():
        return []

    try:
        children = list(root.iterdir())
    except OSError:
        # NOTE(claude): an unreadable root (e.g. permission error) is treated the same as a
        # missing one -- []. There is no project-level error slot in the response to report
        # this in; adding one would be a contract change, out of scope here.
        return []

    projects: list[MemoryProject] = []
    for child in children:
        # NOTE(claude): zero-read rule (spec 2026-08-17 §2, mirrored from
        # ingest/discovery.py's directory-level skip). This route is the only API read path
        # that lists directories and reads file content off disk rather than the captured
        # archive, so it must enforce project exclusion itself -- everywhere else, exclusion is
        # already baked into what got captured. Check the NAME ONLY and move on: no
        # `is_dir()`, `stat()`, `exists()`, or `iterdir()` on an excluded child, ever.
        if child.name in excluded:
            continue
        if not child.is_dir():
            continue
        memory_dir = child / "memory"
        try:
            if not memory_dir.is_dir():
                continue

            candidates = []
            for entry in memory_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix != ".md":
                    continue
                if entry.name == _MEMORY_INDEX_FILENAME:
                    # NOTE(claude): MEMORY.md is the per-project index Claude Code itself
                    # maintains -- the memories board replaces it as the browsing surface, so
                    # it is not listed as one of the memories.
                    continue
                candidates.append(entry)
        except OSError:
            # NOTE(claude): a memory/ dir that can't be listed (e.g. chmod 0o000) is a
            # project skipped, not a request-failing error -- same "no project-level error
            # slot" reasoning as the root.iterdir() guard above.
            continue

        if not candidates:
            continue

        memories = sorted(
            (_read_memory_file(entry) for entry in candidates), key=lambda m: (m.name, m.filename)
        )
        projects.append(MemoryProject(dir_slug=child.name, memories=tuple(memories)))

    projects.sort(key=lambda p: p.dir_slug)
    return projects
