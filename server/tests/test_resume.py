# server/tests/test_resume.py
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect import resume
from introspect.export import SessionNotFoundError, export_transcript
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import Project
from tests.conftest import PROJECT_SLUG_1, SESSION_UUID_1


def test_resume_command_shape() -> None:
    assert resume.build_resume_command("abc-123") == "claude --resume abc-123"


def test_launch_script_happy_shape() -> None:
    script = resume.build_launch_script("/Users/casey/projects/myapp", "abc-123")
    lines = script.splitlines()
    assert lines[0] == "#!/bin/zsh -l"
    assert "cd /Users/casey/projects/myapp || exit 1" in script
    assert "command -v claude" in script
    assert "exec claude --resume abc-123" in script
    assert "pbcopy" in script  # the in-script 4a fallback
    assert script.endswith("\n")


def test_launch_script_quotes_hostile_cwd() -> None:
    # A cwd containing spaces and a single quote must arrive intact and un-executed.
    hostile = "/tmp/it's a dir; rm -rf ~"
    script = resume.build_launch_script(hostile, "abc-123")
    assert "'/tmp/it'\"'\"'s a dir; rm -rf ~'" in script  # shlex.quote form
    assert "cd /tmp/it's" not in script  # never unquoted


# --- resume_session() orchestration --------------------------------------------------------


class FakeOpen:
    """Records `open -a` invocations; scripted exit code/stderr."""

    def __init__(self, code: int = 0, err: str = "") -> None:
        self.calls: list[list[str]] = []
        self.code = code
        self.err = err

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        self.calls.append(argv)
        return (self.code, "", self.err)


@pytest.fixture
def resumable(db_session: Session, fixture_tree: Path, tmp_path: Path) -> dict:
    """Captured archive + a resolved_cwd that actually exists on this machine."""
    for f in discover(fixture_tree):
        capture_file(db_session, f)
    db_session.commit()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    project = db_session.scalars(select(Project).where(Project.dir_slug == PROJECT_SLUG_1)).one()
    project.resolved_cwd = str(workdir)
    db_session.commit()
    return {
        "db": db_session,
        "source_root": fixture_tree,
        "scripts_dir": tmp_path / "scripts",
        "workdir": workdir,
        "live_path": fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl",
    }


def _resume(r: dict, runner: FakeOpen, terminal_app: str = "Terminal"):
    from introspect import resume

    return resume.resume_session(
        r["db"],
        SESSION_UUID_1,
        source_root=r["source_root"],
        terminal_app=terminal_app,
        scripts_dir=r["scripts_dir"],
        runner=runner,
    )


def test_present_file_is_never_touched_and_launches(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    before = resumable["live_path"].read_bytes()
    runner = FakeOpen()
    out = _resume(resumable, runner)
    assert (out.restored, out.launched, out.mode) == (False, True, "launched")
    assert resumable["live_path"].read_bytes() == before
    script_path = resumable["scripts_dir"] / f"{SESSION_UUID_1}.command"
    assert runner.calls == [["open", "-a", "Terminal", str(script_path)]]
    assert script_path.stat().st_mode & 0o755 == 0o755
    assert str(resumable["workdir"]) in script_path.read_text()


def test_missing_file_restored_byte_identical(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    expected = export_transcript(resumable["db"], SESSION_UUID_1)
    resumable["live_path"].unlink()
    out = _resume(resumable, FakeOpen())
    assert (out.restored, out.mode) == (True, "launched")
    assert resumable["live_path"].read_bytes() == expected


def test_deleted_slug_dir_recreated(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    import shutil

    shutil.rmtree(resumable["live_path"].parent)
    out = _resume(resumable, FakeOpen())
    assert out.restored is True
    assert resumable["live_path"].exists()


def test_missing_cwd_no_launch(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    project = resumable["db"].scalars(
        select(Project).where(Project.dir_slug == PROJECT_SLUG_1)
    ).one()
    project.resolved_cwd = str(resumable["workdir"] / "gone")
    resumable["db"].commit()
    runner = FakeOpen()
    out = _resume(resumable, runner)
    assert (out.launched, out.mode) == (False, "missing_cwd")
    assert out.detail == str(resumable["workdir"] / "gone")
    assert out.command == f"claude --resume {SESSION_UUID_1}"
    assert runner.calls == []


def test_open_failure_reported(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    out = _resume(resumable, FakeOpen(code=1, err="Unable to find application"), terminal_app="Nope")
    assert (out.launched, out.mode) == (False, "open_failed")
    assert out.detail == "Unable to find application"


def test_non_darwin_restores_but_never_launches(resumable, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    resumable["live_path"].unlink()
    runner = FakeOpen()
    out = _resume(resumable, runner)
    assert (out.restored, out.launched, out.mode) == (True, False, "unsupported_platform")
    assert runner.calls == []


def test_unknown_session_raises(resumable) -> None:
    from introspect import resume

    with pytest.raises(SessionNotFoundError):
        resume.resume_session(
            resumable["db"],
            "00000000-0000-0000-0000-000000000000",
            source_root=resumable["source_root"],
            terminal_app="Terminal",
            scripts_dir=resumable["scripts_dir"],
            runner=FakeOpen(),
        )
