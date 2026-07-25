# server/tests/test_resume.py
from introspect import resume


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
