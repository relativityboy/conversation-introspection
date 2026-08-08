"""introspect.update against real fixture git repos (bare origin + clone)."""

import subprocess
from pathlib import Path

import pytest

from introspect import update
from introspect.update import UpdateState

CHANGELOG_V1 = "# Changelog\n\n## 1.0.0 — 2026-08-01\n- V1.\n"
CHANGELOG_V2 = (
    "# Changelog\n\n## 1.1.0 — 2026-08-08\n- New thing.\n\n## 1.0.0 — 2026-08-01\n- V1.\n"
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _make_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin with CHANGELOG v1 committed, and a clone tracking it."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "CHANGELOG.md").write_text(CHANGELOG_V1, encoding="utf-8")
    (seed / "server").mkdir()
    (seed / "server" / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "v1")
    subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    return origin, clone


def _push_origin_change(tmp_path: Path, origin: Path, files: dict[str, str], msg: str) -> None:
    """Commit files to origin/main via a scratch clone (origin is bare)."""
    scratch = tmp_path / f"scratch-{msg.replace(' ', '-')}"
    subprocess.run(["git", "clone", str(origin), str(scratch)], check=True, capture_output=True)
    _git(scratch, "config", "user.email", "t@t")
    _git(scratch, "config", "user.name", "t")
    for rel, content in files.items():
        p = scratch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(scratch, "add", "-A")
    _git(scratch, "commit", "-m", msg)
    _git(scratch, "push", "origin", "main")


def test_up_to_date(tmp_path: Path) -> None:
    _, clone = _make_pair(tmp_path)
    chk = update.check(clone)
    assert chk.state is UpdateState.UP_TO_DATE
    assert chk.local_version == "1.0.0"


def test_behind_lists_new_entries(tmp_path: Path) -> None:
    origin, clone = _make_pair(tmp_path)
    _push_origin_change(tmp_path, origin, {"CHANGELOG.md": CHANGELOG_V2}, "v1.1")
    chk = update.check(clone)
    assert chk.state is UpdateState.BEHIND
    assert chk.remote_version == "1.1.0"
    assert [e.version for e in chk.new_entries] == ["1.1.0"]
    assert chk.new_entries[0].bullets == ("New thing.",)


def test_local_ahead_version(tmp_path: Path) -> None:
    _, clone = _make_pair(tmp_path)
    (clone / "CHANGELOG.md").write_text(CHANGELOG_V2, encoding="utf-8")
    _git(clone, "commit", "-am", "local v1.1")
    chk = update.check(clone)
    assert chk.state is UpdateState.LOCAL_AHEAD


def test_preflight_flags_dirty_and_ahead(tmp_path: Path) -> None:
    _, clone = _make_pair(tmp_path)
    assert update.preflight_problems(clone) == []
    (clone / "server" / "code.py").write_text("x = 2\n", encoding="utf-8")
    problems = update.preflight_problems(clone)
    assert any("uncommitted" in p for p in problems)
    _git(clone, "commit", "-am", "local work")
    problems = update.preflight_problems(clone)
    assert any("origin doesn't" in p for p in problems)


def test_apply_streams_and_detects_server_change(tmp_path: Path) -> None:
    origin, clone = _make_pair(tmp_path)
    _push_origin_change(
        tmp_path, origin,
        {"CHANGELOG.md": CHANGELOG_V2, "server/code.py": "x = 3\n"},
        "v1.1 server change",
    )
    fake_script = tmp_path / "fake-update.sh"
    fake_script.write_text("#!/bin/sh\necho pulling\ngit pull --ff-only --quiet\necho done\n")
    fake_script.chmod(0o755)
    lines: list[str] = []
    result = update.apply(clone, emit=lines.append, update_script=fake_script)
    assert result.ok
    assert result.server_changed
    assert "pulling" in lines and "done" in lines


def test_apply_web_only_change_not_server_changed(tmp_path: Path) -> None:
    origin, clone = _make_pair(tmp_path)
    _push_origin_change(tmp_path, origin, {"web/app.ts": "// new\n", "CHANGELOG.md": CHANGELOG_V2}, "web only")
    fake_script = tmp_path / "fake-update.sh"
    fake_script.write_text("#!/bin/sh\ngit pull --ff-only --quiet\n")
    fake_script.chmod(0o755)
    result = update.apply(clone, emit=lambda _line: None, update_script=fake_script)
    assert result.ok
    assert not result.server_changed


def test_git_failure_raises_update_error(tmp_path: Path) -> None:
    lone = tmp_path / "lone"
    (lone / ".git").mkdir(parents=True)  # not a real repo -> git commands fail
    with pytest.raises(update.UpdateError):
        update.check(lone)
