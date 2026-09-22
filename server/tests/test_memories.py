"""Pure unit tests for :mod:`introspect.memories` -- the frontmatter parser and the disk
scanner. Fixture-driven, tmp_path only: this module reads real files on real permissions
(chmod tests), but never the real ``~/.claude/projects`` tree (repo rule)."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest

from introspect.memories import parse_memory_text, scan_memories


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- parse_memory_text -----------------------------------------------------------------


def test_top_level_type_parsed() -> None:
    text = "---\nname: My Memory\ndescription: a note\ntype: feedback\n---\nbody text\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.name == "My Memory"
    assert parsed.description == "a note"
    assert parsed.type == "feedback"
    assert parsed.error is None


def test_nested_metadata_type_parsed() -> None:
    text = "---\nname: Other\ndescription: another note\nmetadata:\n  type: project\n---\nbody\n"
    parsed = parse_memory_text(text, "y.md")
    assert parsed.name == "Other"
    assert parsed.description == "another note"
    assert parsed.type == "project"


def test_quoted_description_double_quotes() -> None:
    text = '---\ndescription: "x y"\n---\nbody\n'
    parsed = parse_memory_text(text, "z.md")
    assert parsed.description == "x y"


def test_quoted_description_single_quotes() -> None:
    text = "---\ndescription: 'x y'\n---\nbody\n"
    parsed = parse_memory_text(text, "z.md")
    assert parsed.description == "x y"


def test_double_quoted_escapes_unescaped() -> None:
    text = '---\ndescription: "cost (\\"minions\\") here"\n---\nbody\n'
    parsed = parse_memory_text(text, "z.md")
    assert parsed.description == 'cost ("minions") here'


def test_single_quoted_doubled_quote_unescaped() -> None:
    text = "---\ndescription: 'it''s'\n---\nbody\n"
    parsed = parse_memory_text(text, "z.md")
    assert parsed.description == "it's"


def test_bare_file_no_frontmatter() -> None:
    text = "# My Title\nsome body text\nmore text\n"
    parsed = parse_memory_text(text, "bare.md")
    assert parsed.name == "bare"
    assert parsed.description == "My Title"
    assert parsed.type is None
    assert parsed.body == text
    assert parsed.error is None


def test_frontmatter_no_name_defaults_to_stem() -> None:
    text = "---\ndescription: d\n---\nbody\n"
    parsed = parse_memory_text(text, "stem-name.md")
    assert parsed.name == "stem-name"


def test_frontmatter_no_description_is_none() -> None:
    text = "---\nname: n\n---\nbody\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.description is None


def test_frontmatter_no_type_is_none() -> None:
    text = "---\nname: n\n---\nbody\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.type is None


def test_empty_name_value_defaults_to_stem() -> None:
    text = "---\nname:\ndescription: d\n---\nbody\n"
    parsed = parse_memory_text(text, "stem-name.md")
    assert parsed.name == "stem-name"


def test_empty_description_value_is_none() -> None:
    text = '---\nname: n\ndescription: ""\n---\nbody\n'
    parsed = parse_memory_text(text, "x.md")
    assert parsed.description is None


def test_empty_type_value_is_none() -> None:
    text = "---\nname: n\ntype:\n---\nbody\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.type is None


def test_unterminated_frontmatter() -> None:
    text = "---\nname: n\nno closing fence here\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.error == "frontmatter unterminated"
    assert parsed.body == text
    assert parsed.name == "x"
    assert parsed.description is None
    assert parsed.type is None


def test_body_excludes_frontmatter_and_leading_blank_lines() -> None:
    text = "---\nname: n\n---\n\n\nactual body starts here\nsecond line\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.body == "actual body starts here\nsecond line\n"


def test_leading_bom_does_not_break_frontmatter_detection() -> None:
    text = "﻿---\ntype: feedback\n---\nbody text\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.type == "feedback"
    assert parsed.body == "body text\n"


def test_fence_with_trailing_whitespace_recognized() -> None:
    text = "--- \nname: n\n---\nbody\n"
    parsed = parse_memory_text(text, "x.md")
    assert parsed.name == "n"
    assert parsed.body == "body\n"


# --- scan_memories -----------------------------------------------------------------------


def test_memory_index_skipped_and_non_md_ignored(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "proj" / "memory" / "a.md", "# A\nbody\n")
    _write(root / "proj" / "memory" / "MEMORY.md", "index\n")
    _write(root / "proj" / "memory" / "notes.txt", "not markdown\n")
    (root / "proj" / "memory" / "subdir").mkdir(parents=True)
    (root / "proj" / "memory" / "subdir" / "b.md").write_text("nested, must be ignored\n")

    projects = scan_memories(root)
    assert len(projects) == 1
    assert [m.filename for m in projects[0].memories] == ["a.md"]


def test_unreadable_file_listed_with_permission_error(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    root = tmp_path / "projects"
    file_path = root / "proj" / "memory" / "secret.md"
    _write(file_path, "# Secret\nbody\n")
    file_path.chmod(0o000)
    try:
        projects = scan_memories(root)
    finally:
        file_path.chmod(0o644)

    assert len(projects) == 1
    memory = projects[0].memories[0]
    assert memory.filename == "secret.md"
    assert memory.body is None
    assert memory.error == "PermissionError"


def test_invalid_utf8_bytes_decoded_with_replacement(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    file_path = root / "proj" / "memory" / "bad.md"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"---\nname: n\n---\nbroken: \xff\xfe bytes\n")

    projects = scan_memories(root)
    memory = projects[0].memories[0]
    assert memory.error == "UnicodeDecodeError"
    assert "�" in memory.body
    assert memory.name == "n"  # frontmatter (pure ASCII) still parsed


def test_excluded_project_never_reads_its_memory_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    root = tmp_path / "projects"
    slug = "excluded-proj"
    project_dir = root / slug
    _write(project_dir / "memory" / "x.md", "# X\nbody\n")
    project_dir.chmod(0o000)

    # The direct proof: spy on every os-level entry point the scanner could reach to touch the
    # filesystem, and record the path each one was called with. A finding-1-style
    # `except OSError: continue` around a later stat/iterdir call would swallow the
    # PermissionError the chmod below causes IF the scanner ever descended into the excluded
    # project -- so a chmod-and-assert-no-exception test alone can't tell "never touched it"
    # apart from "touched it, but the failure got caught." Recording every path closes that gap.
    touched: list[str] = []

    def _record(path: object) -> None:
        try:
            touched.append(os.fspath(path))
        except TypeError:
            pass  # not a path-like arg (e.g. a file descriptor int) -- not a tree touch

    original_stat = os.stat
    original_lstat = os.lstat
    original_listdir = os.listdir
    original_scandir = os.scandir
    original_open = os.open

    def spy_stat(path, *args, **kwargs):
        _record(path)
        return original_stat(path, *args, **kwargs)

    def spy_lstat(path, *args, **kwargs):
        _record(path)
        return original_lstat(path, *args, **kwargs)

    def spy_listdir(path=".", *args, **kwargs):
        _record(path)
        return original_listdir(path, *args, **kwargs)

    def spy_scandir(path=".", *args, **kwargs):
        _record(path)
        return original_scandir(path, *args, **kwargs)

    def spy_open(path, *args, **kwargs):
        _record(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", spy_stat)
    monkeypatch.setattr(os, "lstat", spy_lstat)
    monkeypatch.setattr(os, "listdir", spy_listdir)
    monkeypatch.setattr(os, "scandir", spy_scandir)
    monkeypatch.setattr(os, "open", spy_open)

    try:
        projects = scan_memories(root, excluded=frozenset({slug}))
    finally:
        monkeypatch.undo()
        project_dir.chmod(0o755)

    forbidden = str(project_dir)
    forbidden_prefix = forbidden + os.sep
    assert not any(p == forbidden or p.startswith(forbidden_prefix) for p in touched), touched

    # Extra tripwire, kept alongside the spy: the project dir is genuinely unreadable, so if the
    # spy assertion above were ever satisfied by a differently-shaped path that still reached
    # into the excluded project, the real filesystem call would raise. scan_memories must not
    # have raised either.
    assert projects == []


def test_unreadable_memory_dir_is_skipped_other_project_still_listed(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    root = tmp_path / "projects"
    _write(root / "proj-a" / "memory" / "a.md", "# A\nbody\n")
    _write(root / "proj-b" / "memory" / "b.md", "# B\nbody\n")
    blocked = root / "proj-a" / "memory"
    blocked.chmod(0o000)
    try:
        projects = scan_memories(root)
    finally:
        blocked.chmod(0o755)

    # proj-a's memory/ dir can't be listed (chmod 0o000): it must be skipped, not raise and
    # 500 the whole request. proj-b is unaffected and still shows up.
    assert [p.dir_slug for p in projects] == ["proj-b"]


def test_projects_and_memories_sorted(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "zeta-proj" / "memory" / "x.md", "# irrelevant\n")
    _write(root / "alpha-proj" / "memory" / "y.md", "# irrelevant\n")
    _write(root / "alpha-proj" / "memory" / "zzz-file.md", "---\nname: aaa-name\n---\nbody\n")
    _write(root / "alpha-proj" / "memory" / "aaa-file.md", "---\nname: zzz-name\n---\nbody\n")

    projects = scan_memories(root)
    assert [p.dir_slug for p in projects] == ["alpha-proj", "zeta-proj"]

    alpha = next(p for p in projects if p.dir_slug == "alpha-proj")
    # Sorted by the parsed `name` field, not filename: "aaa-file.md" carries name
    # "zzz-name" and must sort LAST despite its filename sorting first.
    assert [m.name for m in alpha.memories] == ["aaa-name", "y", "zzz-name"]


def test_duplicate_names_sorted_by_filename(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "proj" / "memory" / "b.md", "---\nname: same\n---\nbody\n")
    _write(root / "proj" / "memory" / "a.md", "---\nname: same\n---\nbody\n")

    projects = scan_memories(root)
    assert [m.filename for m in projects[0].memories] == ["a.md", "b.md"]


def test_projects_with_no_listable_memories_omitted(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _write(root / "only-index" / "memory" / "MEMORY.md", "index\n")
    (root / "empty-memory" / "memory").mkdir(parents=True)
    (root / "no-memory-dir").mkdir(parents=True)
    _write(root / "has-content" / "memory" / "note.md", "# Note\n")

    projects = scan_memories(root)
    assert [p.dir_slug for p in projects] == ["has-content"]


def test_body_mtime_and_path_facts(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    file_path = root / "proj" / "memory" / "note.md"
    _write(file_path, "---\nname: n\n---\n\nactual body\n")

    projects = scan_memories(root)
    memory = projects[0].memories[0]
    assert memory.body == "actual body\n"
    assert memory.mtime is not None
    assert memory.mtime.tzinfo is not None
    assert memory.mtime.utcoffset() == timedelta(0)
    assert Path(memory.path).is_absolute()
    assert memory.path == str(file_path.resolve())
