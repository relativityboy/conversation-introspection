"""Manage a single scheduled-import line in the user's crontab (macOS-first, crontab-based).

We own EXACTLY ONE crontab line, identified by its trailing marker::

    */15 * * * * /abs/path/introspect import >> ~/.conversation-introspection/cron.log 2>&1  # introspect-import (managed by introspect)

Every other line in the crontab -- env vars, comments, unrelated jobs -- is preserved
byte-for-byte across install/remove; we only ever add, replace, or delete our own marked line.

The module is split the way the rest of the package is: pure text functions (``schedule_expr``,
``build_line``, ``apply_install``, ``apply_remove``, ``find_managed_line``, ``parse_minutes``)
that operate on crontab text and are trivially unit-testable, and a thin subprocess edge
(:class:`CrontabIO`) that shells out to ``crontab -l`` / ``crontab -``. The subprocess call is
injectable (``CrontabIO(runner=...)``) so no test ever touches the real crontab. The three
operations (:func:`status`, :func:`install`, :func:`remove`) compose the two layers.

No ``textual`` (or any heavy) import here: the CLI wires these verbs directly and the TUI reuses
the same functions in-process, so this stays cheap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from introspect import config

#: The trailing marker that identifies OUR line. A crontab line is ours iff (ignoring trailing
#: whitespace) it ends with this exact string. Nothing else is ever touched.
MARKER = "# introspect-import (managed by introspect)"

#: Default log the cron job appends its output to. Same directory as the archive DB, so it lives
#: alongside everything else introspect writes.
DEFAULT_LOG = config.DEFAULT_DB.parent / "cron.log"

#: Console-script / executable name to schedule and to look up on PATH.
BINARY_NAME = "introspect"

#: A subprocess call: ``(argv, stdin_text) -> (returncode, stdout, stderr)``. Injectable so the
#: whole edge can be driven by a double in tests.
Runner = Callable[[list[str], "str | None"], "tuple[int, str, str]"]

#: Shown once, by the caller, before the first ``crontab`` subprocess call a command invocation
#: makes -- macOS only. macOS gates ``crontab`` (even a read-only ``-l``) behind a TCC
#: "administer this computer" dialog the first time a given terminal app invokes it, and nothing
#: about ``crontab`` itself warns the user first -- so an unwarned, security-minded user
#: reasonably distrusts us. Same pattern as ``introspect.tui.webserver.PUBLIC_BIND_WARNING``: the
#: text lives here, but printing/emitting it is the CLI/TUI layer's job (see module docstring) --
#: this module never writes to stdout or the TUI log itself.
MACOS_PERMISSION_NOTICE = (
    "macOS may ask for permission -- that's the OS confirming your terminal may manage "
    "scheduled jobs (one-time grant)."
)


def macos_permission_notice() -> str | None:
    """:data:`MACOS_PERMISSION_NOTICE` on macOS, ``None`` everywhere else.

    Callers print/emit this once per command invocation, right before the first call that will
    actually touch the crontab -- not before every individual subprocess call within it (e.g.
    ``install`` issues a read AND a write, but the notice is still shown only once).
    """
    return MACOS_PERMISSION_NOTICE if sys.platform == "darwin" else None


class CronError(Exception):
    """A cron operation could not be completed (bad interval, unusable binary, crontab I/O)."""


@dataclass(frozen=True)
class CronStatus:
    """The state of our managed line: whether it is present, its interval, and its exact text."""

    installed: bool
    minutes: int | None
    line: str | None


# --- Pure text logic ----------------------------------------------------------------------


def schedule_expr(minutes: int) -> str:
    """Render the five-field cron schedule for a run every ``minutes`` minutes.

    ``1..59`` -> ``*/N * * * *``; ``60`` -> ``0 * * * *`` (top of every hour). Anything outside
    ``1..60`` is a :class:`CronError` -- the sub-hour ``*/N`` form is only meaningful for N<60.
    """
    if minutes == 60:
        return "0 * * * *"
    if 1 <= minutes <= 59:
        return f"*/{minutes} * * * *"
    raise CronError(f"interval must be an integer 1-60 minutes, got {minutes}")


def build_line(binary: Path, minutes: int, log_path: Path) -> str:
    """Build our full managed crontab line (no trailing newline)."""
    return f"{schedule_expr(minutes)} {binary} import >> {log_path} 2>&1  {MARKER}"


def _is_managed(line: str) -> bool:
    return line.rstrip().endswith(MARKER)


def find_managed_line(text: str) -> str | None:
    """Return our managed line (without its newline), or ``None`` if the crontab has none."""
    for line in text.splitlines():
        if _is_managed(line):
            return line
    return None


def parse_minutes(line: str) -> int | None:
    """Recover the interval in minutes from a managed line, or ``None`` if unrecognisable.

    Inverse of :func:`schedule_expr`: ``*/N * * * *`` -> N, ``0 * * * *`` -> 60.
    """
    fields = line.split()
    if not fields:
        return None
    minute = fields[0]
    if minute == "0":
        return 60
    if minute.startswith("*/"):
        try:
            return int(minute[2:])
        except ValueError:
            return None
    return None


def apply_install(text: str, binary: Path, minutes: int, log_path: Path) -> str:
    """Return ``text`` with our line added or replaced, preserving every other line byte-for-byte.

    If a managed line already exists it is replaced IN PLACE (same position, new interval) so we
    never accumulate a second line. Otherwise the line is appended; a final line that lacks a
    newline first gets one, so our entry always lands on its own line.
    """
    new_line = build_line(binary, minutes, log_path) + "\n"
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _is_managed(line):
            lines[i] = new_line
            return "".join(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    lines.append(new_line)
    return "".join(lines)


def apply_remove(text: str) -> str:
    """Return ``text`` with our managed line removed; every other line is preserved exactly.

    An empty string is a legitimate result (the crontab held only our line) and callers must
    still write it, so the now-empty crontab is persisted rather than left stale.
    """
    kept = [line for line in text.splitlines(keepends=True) if not _is_managed(line)]
    return "".join(kept)


# --- Binary resolution --------------------------------------------------------------------


def resolve_binary(
    *, argv0: str | None = None, which: Callable[[str], str | None] | None = None
) -> Path | None:
    """Absolute path to the running ``introspect`` executable, or ``None`` if it can't be found.

    Prefers ``sys.argv[0]`` when it is a real path to our console script (basename ``introspect``,
    contains a separator, exists as a file) -- that is the exact binary this process was launched
    as, which is what a cron entry should invoke. Otherwise falls back to a PATH lookup. A bare
    ``argv0`` of ``"introspect"`` (no separator, e.g. resolved from PATH already) is not trusted
    as a filesystem path and routes through the PATH lookup instead.
    """
    argv0 = sys.argv[0] if argv0 is None else argv0
    which = shutil.which if which is None else which
    if argv0 and os.sep in argv0:
        candidate = Path(argv0)
        if candidate.name == BINARY_NAME:
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
    found = which(BINARY_NAME)
    return Path(found).resolve() if found else None


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


# --- Subprocess edge ----------------------------------------------------------------------


def _subprocess_runner(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        # `crontab` itself is not installed / not on PATH.
        return (127, "", f"{argv[0]}: command not found")
    return (proc.returncode, proc.stdout, proc.stderr)


class CrontabIO:
    """Reads and writes the user crontab via the ``crontab`` binary. The subprocess is injectable.

    ``crontab -l`` exits non-zero with a "no crontab for <user>" message when the user has no
    crontab; that is an empty crontab, not an error, so :meth:`read` maps it to ``""``. Any OTHER
    non-zero read is a real failure and raises -- treating it as "empty" and then writing would
    silently drop the user's existing entries.
    """

    def __init__(self, runner: Runner | None = None) -> None:
        self._run = runner or _subprocess_runner

    def read(self) -> str:
        code, out, err = self._run(["crontab", "-l"], None)
        if code == 0:
            return out
        if "no crontab" in err.lower():
            return ""
        raise CronError(f"could not read crontab: {err.strip() or f'crontab -l exited {code}'}")

    def write(self, text: str) -> None:
        code, _out, err = self._run(["crontab", "-"], text)
        if code != 0:
            raise CronError(
                f"could not write crontab: {err.strip() or f'crontab - exited {code}'}"
            )


# --- Operations ---------------------------------------------------------------------------


def status(io: CrontabIO) -> CronStatus:
    """Report whether our managed line is present, and if so its interval and exact text."""
    line = find_managed_line(io.read())
    if line is None:
        return CronStatus(installed=False, minutes=None, line=None)
    return CronStatus(installed=True, minutes=parse_minutes(line), line=line)


def install(
    io: CrontabIO,
    *,
    minutes: int = 15,
    binary: Path | None = None,
    log_path: Path | None = None,
) -> CronStatus:
    """Add or replace our managed line so ``introspect import`` runs every ``minutes`` minutes.

    Refuses (``CronError``) before writing anything if the interval is out of range or the
    resolved binary does not exist / is not executable -- a broken cron line is worse than none.
    Ensures the log's parent directory exists so the ``>>`` redirect can open it on the first run.
    """
    schedule_expr(minutes)  # validate the interval up front, before any crontab or binary work
    binary = resolve_binary() if binary is None else Path(binary)
    if binary is None:
        raise CronError(
            "could not locate the introspect executable to schedule "
            f"(is '{BINARY_NAME}' on PATH?)"
        )
    if not _is_executable(binary):
        raise CronError(f"refusing to install: {binary} is not an executable file")
    log_path = DEFAULT_LOG if log_path is None else Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    io.write(apply_install(io.read(), binary, minutes, log_path))
    return CronStatus(installed=True, minutes=minutes, line=build_line(binary, minutes, log_path))


def remove(io: CrontabIO) -> bool:
    """Delete our managed line. Returns ``True`` iff a line was actually present and removed.

    When our line was the only entry, the (now empty) crontab is still written so it does not go
    stale. When our line is absent this is a no-op -- no crontab write is issued at all.
    """
    text = io.read()
    if find_managed_line(text) is None:
        return False
    io.write(apply_remove(text))
    return True


# --- Rendering ----------------------------------------------------------------------------


def status_line(st: CronStatus) -> str:
    """Compact one-liner shared by ``introspect cron status`` and the TUI ``/status`` line."""
    if not st.installed:
        return "cron: not installed"
    label = f"{st.minutes}m" if st.minutes is not None else "?"
    return f"cron: installed@{label}"
