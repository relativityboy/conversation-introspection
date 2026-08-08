"""CHANGELOG.md as the single source of truth for the app's release version.

The top entry IS the current version (no git tags; nothing else to keep in sync).
Grammar: an entry heading is ``## <MAJOR.MINOR.PATCH> — <YYYY-MM-DD>`` (em-dash
canonical, plain hyphen accepted); ``- `` lines beneath it are that version's
user-facing changelist; prose before the first heading is ignored.

The parser RAISES on malformed input; runtime surfaces call :func:`app_version`,
which degrades to ``"unknown"`` instead — a broken changelog must never take down
serving or updating, but it must not pass silently either (each surface prints a
visible note when it sees ``"unknown"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) [—-] (\d{4}-\d{2}-\d{2})\s*$")


class ChangelogError(ValueError):
    """CHANGELOG.md content that violates the entry grammar."""


@dataclass(frozen=True)
class Entry:
    version: str
    date: str
    bullets: tuple[str, ...]


def parse_changelog(text: str) -> list[Entry]:
    entries: list[Entry] = []
    current: tuple[str, str] | None = None
    bullets: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            if current is not None:
                entries.append(Entry(current[0], current[1], tuple(bullets)))
            current = (match.group(1), match.group(2))
            bullets = []
        elif line.startswith("## "):
            raise ChangelogError(f"malformed changelog heading: {line!r}")
        elif current is not None and line.startswith("- "):
            bullets.append(line[2:].strip())
    if current is not None:
        entries.append(Entry(current[0], current[1], tuple(bullets)))
    if not entries:
        raise ChangelogError("no release entries found")
    return entries


def current_version(text: str) -> str:
    return parse_changelog(text)[0].version


def entries_newer_than(entries: list[Entry], version: str) -> list[Entry] | None:
    """Entries strictly newer than ``version`` (list order = newest first).

    Returns ``None`` when ``version`` does not appear at all — the caller cannot
    know what "newer" means then (e.g. a local checkout ahead of origin).
    """
    for i, entry in enumerate(entries):
        if entry.version == version:
            return entries[:i]
    return None


def find_changelog(start: Path) -> Path | None:
    """Walk from ``start`` up its ancestors for CHANGELOG.md.

    Same boundary rule as ``_walk_up_for_ui_dist`` (api/__init__.py): the first
    ancestor containing ``.git`` is still checked, but the walk never ascends
    past it, so an unrelated CHANGELOG.md above the repo is never picked up.
    """
    for ancestor in (start, *start.parents):
        candidate = ancestor / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
        if (ancestor / ".git").exists():
            return None
    return None


def app_version(start: Path | None = None) -> str:
    """Best-effort current version for runtime surfaces; ``"unknown"`` on any failure."""
    origin = start if start is not None else Path(__file__).resolve()
    try:
        path = find_changelog(origin)
        if path is None:
            return "unknown"
        return current_version(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
