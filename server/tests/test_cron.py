"""Cron management tests (Phase 4 F9): marker-line ownership over the user crontab.

Two layers, mirroring the module's own split of pure logic from the subprocess edge:

  * Pure text functions -- add/replace/remove our single managed line on fixture crontabs,
    byte-for-byte preservation of everyone else's lines, and interval rendering. No subprocess,
    no filesystem (except a throwaway executable to satisfy the install guard).
  * The subprocess edge (:class:`~introspect.cron.CrontabIO`) driven with an injected runner
    double so the real ``crontab`` binary is NEVER touched -- the no-crontab read path and the
    exact bytes handed to ``crontab -`` on write are asserted directly.

``FakeRunner`` and ``make_exec`` are imported by the CLI and TUI cron tests too (the same way
``FakeWeb`` is shared), so no other test module ever shells out to a real crontab either.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from introspect import cron

MARKER = "# introspect-import (managed by introspect)"
BINARY = Path("/opt/venv/bin/introspect")
LOG = Path("/home/u/.conversation-introspection/cron.log")

EMPTY = ""

# A populated crontab: an env var, comments, a blank line, and unrelated jobs. This is the
# byte-for-byte preservation contract -- none of these bytes may change across install/remove.
POPULATED = (
    "MAILTO=me@example.com\n"
    "# nightly backup\n"
    "0 2 * * * /usr/local/bin/backup --full\n"
    "\n"
    "*/30 * * * * /usr/bin/ping-thing\n"
)


def make_exec(dir_path: Path, name: str = "introspect") -> Path:
    """Create a real, executable throwaway file so the install guard accepts it."""
    p = dir_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


class FakeRunner:
    """Injectable stand-in for the subprocess call. Scripts the read; records every write.

    Signature matches :data:`introspect.cron.Runner`: ``(argv, stdin) -> (code, out, err)``. A
    write persists into the readable state (as a real crontab does), so multi-call flows -- CLI
    ``install`` then ``status`` then ``remove`` sharing one runner -- see their own writes.
    """

    def __init__(self, *, read_text: str = "", read_code: int = 0, read_err: str = "") -> None:
        self._read_text = read_text
        self._read_code = read_code
        self._read_err = read_err
        self.written: list[str] = []
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        self.calls.append(argv)
        if argv[:2] == ["crontab", "-l"]:
            return (self._read_code, self._read_text, self._read_err)
        if argv[:2] == ["crontab", "-"]:
            self.written.append(stdin or "")
            # A successful write establishes the crontab: later reads return exactly this.
            self._read_text = stdin or ""
            self._read_code = 0
            self._read_err = ""
            return (0, "", "")
        raise AssertionError(f"unexpected argv {argv}")


def _line(minutes: int = 15) -> str:
    return cron.build_line(BINARY, minutes, LOG)


# --- Interval rendering -------------------------------------------------------------------


def test_schedule_expr_sub_hour() -> None:
    assert cron.schedule_expr(15) == "*/15 * * * *"
    assert cron.schedule_expr(5) == "*/5 * * * *"
    assert cron.schedule_expr(1) == "*/1 * * * *"


def test_schedule_expr_hourly_is_60() -> None:
    assert cron.schedule_expr(60) == "0 * * * *"


def test_schedule_expr_rejects_out_of_range() -> None:
    for bad in (0, -1, 61, 120):
        with pytest.raises(cron.CronError):
            cron.schedule_expr(bad)


def test_build_line_shape() -> None:
    assert _line(15) == f"*/15 * * * * {BINARY} import >> {LOG} 2>&1  {MARKER}"
    assert _line(15).endswith(MARKER)
    assert _line(60).startswith("0 * * * * ")


# --- Add / replace / remove round-trips ---------------------------------------------------


def test_install_into_empty_crontab() -> None:
    out = cron.apply_install(EMPTY, BINARY, 15, LOG)
    assert out == _line(15) + "\n"
    assert cron.find_managed_line(out) == _line(15)


def test_install_appends_to_populated_preserving_others() -> None:
    out = cron.apply_install(POPULATED, BINARY, 15, LOG)
    assert out == POPULATED + _line(15) + "\n"
    assert out.startswith(POPULATED)  # nobody else's bytes moved


def test_install_replace_not_duplicate() -> None:
    once = cron.apply_install(POPULATED, BINARY, 15, LOG)
    twice = cron.apply_install(once, BINARY, 15, LOG)
    assert twice == once
    assert once.count(MARKER) == twice.count(MARKER) == 1


def test_install_interval_change_replaces_in_place() -> None:
    at15 = cron.apply_install(POPULATED, BINARY, 15, LOG)
    at5 = cron.apply_install(at15, BINARY, 5, LOG)
    assert at5.count(MARKER) == 1
    assert cron.find_managed_line(at5) == _line(5)
    assert at5 == POPULATED + _line(5) + "\n"  # position preserved (still last)


def test_install_replaces_line_in_the_middle() -> None:
    text = f"A_VAR=1\n{_line(15)}\n0 3 * * * job\n"
    out = cron.apply_install(text, BINARY, 5, LOG)
    assert out == f"A_VAR=1\n{_line(5)}\n0 3 * * * job\n"


def test_install_adds_separator_newline_when_last_line_lacks_one() -> None:
    no_nl = "0 2 * * * job"  # a crontab whose last line is not newline-terminated
    out = cron.apply_install(no_nl, BINARY, 15, LOG)
    assert out == "0 2 * * * job\n" + _line(15) + "\n"


def test_remove_deletes_only_our_line() -> None:
    with_ours = cron.apply_install(POPULATED, BINARY, 15, LOG)
    assert cron.apply_remove(with_ours) == POPULATED  # byte-for-byte back to original
    assert MARKER not in cron.apply_remove(with_ours)


def test_remove_absent_is_noop_byte_for_byte() -> None:
    assert cron.apply_remove(POPULATED) == POPULATED


def test_remove_when_ours_is_the_only_line_yields_empty() -> None:
    only = cron.apply_install(EMPTY, BINARY, 15, LOG)
    assert cron.apply_remove(only) == ""


def test_install_then_remove_round_trips_populated() -> None:
    assert cron.apply_remove(cron.apply_install(POPULATED, BINARY, 15, LOG)) == POPULATED


# --- find / parse -------------------------------------------------------------------------


def test_find_managed_line_absent() -> None:
    assert cron.find_managed_line(POPULATED) is None


def test_parse_minutes_round_trips_every_interval() -> None:
    assert cron.parse_minutes(_line(15)) == 15
    assert cron.parse_minutes(_line(5)) == 5
    assert cron.parse_minutes(_line(60)) == 60


# --- resolve_binary -----------------------------------------------------------------------


def test_resolve_binary_prefers_a_path_argv0(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    got = cron.resolve_binary(argv0=str(binary), which=lambda name: None)
    assert got == binary.resolve()


def test_resolve_binary_ignores_non_introspect_argv0_falls_to_which(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    got = cron.resolve_binary(argv0="/usr/bin/pytest", which=lambda name: str(binary))
    assert got == binary.resolve()


def test_resolve_binary_ignores_bare_name_argv0(tmp_path: Path) -> None:
    # A bare "introspect" (no path separator) is not trusted as a path; PATH lookup wins.
    binary = make_exec(tmp_path)
    got = cron.resolve_binary(argv0="introspect", which=lambda name: str(binary))
    assert got == binary.resolve()


def test_resolve_binary_none_when_nothing_found() -> None:
    assert cron.resolve_binary(argv0="/usr/bin/pytest", which=lambda name: None) is None


# --- CrontabIO: the subprocess edge -------------------------------------------------------


def test_read_returns_crontab_text() -> None:
    io = cron.CrontabIO(runner=FakeRunner(read_text=POPULATED))
    assert io.read() == POPULATED


def test_read_no_crontab_exit_1_is_empty() -> None:
    runner = FakeRunner(read_code=1, read_err="crontab: no crontab for testuser")
    assert cron.CrontabIO(runner=runner).read() == ""


def test_read_unexpected_failure_raises() -> None:
    runner = FakeRunner(read_code=1, read_err="crontab: you are not allowed to use this program")
    with pytest.raises(cron.CronError):
        cron.CrontabIO(runner=runner).read()


def test_write_sends_exact_text_to_stdin() -> None:
    runner = FakeRunner()
    cron.CrontabIO(runner=runner).write(POPULATED)
    assert runner.written == [POPULATED]
    assert runner.calls[-1][:2] == ["crontab", "-"]


def test_write_failure_raises() -> None:
    def failing(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        return (1, "", "crontab: cannot write")

    with pytest.raises(cron.CronError):
        cron.CrontabIO(runner=failing).write("anything")


# --- Operations over the edge (status / install / remove) ---------------------------------


def test_status_not_installed() -> None:
    st = cron.status(cron.CrontabIO(runner=FakeRunner(read_text=POPULATED)))
    assert st == cron.CronStatus(installed=False, minutes=None, line=None)


def test_status_installed_reports_interval_and_full_line(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    seeded = cron.apply_install(POPULATED, binary, 15, LOG)
    st = cron.status(cron.CrontabIO(runner=FakeRunner(read_text=seeded)))
    assert st.installed is True
    assert st.minutes == 15
    assert st.line == cron.build_line(binary, 15, LOG)


def test_install_writes_expected_text_and_ensures_log_dir(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    log = tmp_path / "sub" / "cron.log"
    runner = FakeRunner(read_text=POPULATED)
    st = cron.install(cron.CrontabIO(runner=runner), minutes=5, binary=binary, log_path=log)
    assert runner.written == [POPULATED + cron.build_line(binary, 5, log) + "\n"]
    assert st.installed and st.minutes == 5
    assert log.parent.is_dir()  # the log's parent dir was created so the >> redirect can open it


def test_install_refuses_missing_binary(tmp_path: Path) -> None:
    io = cron.CrontabIO(runner=FakeRunner(read_text=EMPTY))
    with pytest.raises(cron.CronError):
        cron.install(io, minutes=15, binary=tmp_path / "nope", log_path=tmp_path / "l.log")


def test_install_refuses_non_executable_binary(tmp_path: Path) -> None:
    plain = tmp_path / "introspect"
    plain.write_text("not executable")  # exists but has no +x bit
    io = cron.CrontabIO(runner=FakeRunner(read_text=EMPTY))
    with pytest.raises(cron.CronError):
        cron.install(io, minutes=15, binary=plain, log_path=tmp_path / "l.log")


def test_install_refuses_when_binary_unresolvable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cron, "resolve_binary", lambda **k: None)
    io = cron.CrontabIO(runner=FakeRunner(read_text=EMPTY))
    with pytest.raises(cron.CronError):
        cron.install(io, minutes=15, log_path=tmp_path / "l.log")


def test_install_rejects_bad_interval_before_touching_crontab(tmp_path: Path) -> None:
    runner = FakeRunner(read_text=EMPTY)
    with pytest.raises(cron.CronError):
        cron.install(cron.CrontabIO(runner=runner), minutes=0, log_path=tmp_path / "l.log")
    assert runner.written == []  # refused before any write


def test_remove_writes_crontab_without_our_line(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    seeded = cron.apply_install(POPULATED, binary, 15, LOG)
    runner = FakeRunner(read_text=seeded)
    assert cron.remove(cron.CrontabIO(runner=runner)) is True
    assert runner.written == [POPULATED]


def test_remove_absent_returns_false_and_does_not_write() -> None:
    runner = FakeRunner(read_text=POPULATED)
    assert cron.remove(cron.CrontabIO(runner=runner)) is False
    assert runner.written == []  # nothing to remove -> no crontab write at all


def test_remove_last_line_still_writes_empty(tmp_path: Path) -> None:
    binary = make_exec(tmp_path)
    only = cron.apply_install(EMPTY, binary, 15, LOG)
    runner = FakeRunner(read_text=only)
    assert cron.remove(cron.CrontabIO(runner=runner)) is True
    assert runner.written == [""]  # crontab emptied -> the empty result is still written


# --- status_line renderer (shared by CLI status + TUI /status) ----------------------------


def test_status_line_not_installed() -> None:
    assert cron.status_line(cron.CronStatus(False, None, None)) == "cron: not installed"


def test_status_line_installed() -> None:
    st = cron.CronStatus(True, 15, f"*/15 * * * * x import >> y 2>&1  {MARKER}")
    assert cron.status_line(st) == "cron: installed@15m"
