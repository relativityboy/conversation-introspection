"""Changelog parsing: CHANGELOG.md is the single source of truth for the release version."""

import re
from pathlib import Path

import pytest

from introspect.changelog import (
    ChangelogError,
    Entry,
    app_entries,
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


def test_app_entries_best_effort(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")
    entries = app_entries(repo / "server")
    assert entries is not None
    assert [e.version for e in entries] == ["1.2.0", "1.1.0", "1.0.0"]

    (repo / "CHANGELOG.md").write_text("garbage\n", encoding="utf-8")
    assert app_entries(repo / "server") is None

    nowhere = tmp_path / "nowhere"
    (nowhere / ".git").mkdir(parents=True)
    assert app_entries(nowhere) is None


def test_app_version_handles_invalid_utf8(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_bytes(b"## 1.0.0 \xe2\x80\x94 2026-08-08\n- \xff\xfe\n")
    assert app_version(repo / "server") == "unknown"


def test_repo_changelog_conforms() -> None:
    """Repo-content lint, not a unit test: unlike every test above, this DELIBERATELY reads the
    real CHANGELOG.md at the repo root rather than a fixture string, so a hand-edited entry that
    breaks the grammar (or drops out of sync with the web build's separate extraction) fails CI
    instead of surfacing only at runtime as a silent 'unknown' version.
    """
    path = find_changelog(Path(__file__).resolve())
    assert path is not None, "repo CHANGELOG.md not found by find_changelog"
    text = path.read_text(encoding="utf-8")

    entries = parse_changelog(text)
    assert len(entries) >= 3
    for entry in entries:
        assert len(entry.bullets) >= 1, f"entry {entry.version} has no bullets"

    # Pin cross-extractor agreement: web/vite.config.ts's changelogVersion() extracts the top
    # version with this same regex (independently, in TypeScript, at build time) so the UI can
    # bake its own version without duplicating CHANGELOG.md's parser. If the two ever disagree,
    # the version chip in the reading room's status bar would silently lie.
    vite_match = re.search(r"^## (\d+\.\d+\.\d+) ", text, re.MULTILINE)
    assert vite_match is not None
    assert vite_match.group(1) == entries[0].version
