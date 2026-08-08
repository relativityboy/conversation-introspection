"""Changelog parsing: CHANGELOG.md is the single source of truth for the release version."""

from pathlib import Path

import pytest

from introspect.changelog import (
    ChangelogError,
    Entry,
    app_version,
    current_version,
    entries_newer_than,
    find_changelog,
    parse_changelog,
)

WELL_FORMED = """\
# Changelog

Prose preamble is ignored.

## 1.2.0 — 2026-08-08
- Versions everywhere.
- `/update` in the TUI.

## 1.1.0 - 2026-08-07
- Authorship labels.

## 1.0.0 — 2026-08-01
- V1.
"""


def test_parses_entries_in_order_with_bullets() -> None:
    entries = parse_changelog(WELL_FORMED)
    assert [e.version for e in entries] == ["1.2.0", "1.1.0", "1.0.0"]
    assert entries[0] == Entry(
        version="1.2.0",
        date="2026-08-08",
        bullets=("Versions everywhere.", "`/update` in the TUI."),
    )
    # hyphen-separated heading (1.1.0) parses the same as em-dash
    assert entries[1].date == "2026-08-07"


def test_current_version_is_top_entry() -> None:
    assert current_version(WELL_FORMED) == "1.2.0"


def test_malformed_heading_raises() -> None:
    with pytest.raises(ChangelogError):
        parse_changelog("## not-a-version — 2026-08-08\n- x\n")


def test_no_entries_raises() -> None:
    with pytest.raises(ChangelogError):
        parse_changelog("# Changelog\n\njust prose\n")
    with pytest.raises(ChangelogError):
        parse_changelog("")


def test_entries_newer_than() -> None:
    entries = parse_changelog(WELL_FORMED)
    newer = entries_newer_than(entries, "1.0.0")
    assert newer is not None
    assert [e.version for e in newer] == ["1.2.0", "1.1.0"]
    assert entries_newer_than(entries, "1.2.0") == []
    assert entries_newer_than(entries, "0.9.0") is None  # unknown local version


def test_find_changelog_walks_up_and_stops_at_git_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")
    deep = repo / "server" / "src" / "introspect"
    deep.mkdir(parents=True)
    assert find_changelog(deep) == repo / "CHANGELOG.md"

    # no changelog anywhere inside the repo boundary -> None (never ascends past .git)
    bare = tmp_path / "bare"
    (bare / ".git").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")  # OUTSIDE the repo
    inner = bare / "server"
    inner.mkdir()
    assert find_changelog(inner) is None


def test_app_version_best_effort(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")
    assert app_version(repo / "server") == "1.2.0"

    (repo / "CHANGELOG.md").write_text("garbage\n", encoding="utf-8")
    assert app_version(repo / "server") == "unknown"

    nowhere = tmp_path / "nowhere"
    (nowhere / ".git").mkdir(parents=True)
    assert app_version(nowhere) == "unknown"


def test_app_version_handles_invalid_utf8(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_bytes(b"## 1.0.0 \xe2\x80\x94 2026-08-08\n- \xff\xfe\n")
    assert app_version(repo / "server") == "unknown"
