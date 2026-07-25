"""Resume links (spec §17): restore a session's live .jsonl and launch `claude --resume`.

cron.py architecture: pure text builders + an orchestration function whose subprocess edge is an
injectable Runner. The launch artifact is a `.command` script executed by the terminal app via
LaunchServices (`open -a`) — deliberately NOT AppleScript, so no TCC Automation prompt. The script
runs `#!/bin/zsh -l` (login shell): `claude` resolves against the USER's PATH, not the server's,
and the claude-not-on-PATH fallback (pbcopy + message) executes in the only environment where
launchability is actually decidable (§17.1.4).
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

MODE_LAUNCHED = "launched"
MODE_MISSING_CWD = "missing_cwd"
MODE_OPEN_FAILED = "open_failed"
MODE_UNSUPPORTED = "unsupported_platform"


@dataclass(frozen=True)
class ResumeOutcome:
    """What actually happened. `mode` encodes the LAUNCH outcome only; `restored` is orthogonal
    (§17.3) — the UI composes the two."""

    restored: bool
    launched: bool
    mode: str
    command: str
    cwd: str | None
    live_path: str
    detail: str | None


def build_resume_command(session_uuid: str) -> str:
    return f"claude --resume {session_uuid}"


def build_launch_script(cwd: str, session_uuid: str) -> str:
    command = build_resume_command(session_uuid)
    return (
        "#!/bin/zsh -l\n"
        f"cd {shlex.quote(cwd)} || exit 1\n"
        "if command -v claude >/dev/null 2>&1; then\n"
        f"  exec claude --resume {shlex.quote(session_uuid)}\n"
        "else\n"
        f"  printf '%s' {shlex.quote(command)} | pbcopy\n"
        "  echo 'claude not found on PATH -- resume command copied to clipboard.'\n"
        "fi\n"
    )


def _subprocess_runner(argv: list[str], stdin: str | None) -> tuple[int, str, str]:
    # Same shape as cron._subprocess_runner; duplicated (7 lines) rather than importing a
    # private from a sibling module.
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        return (127, "", f"{argv[0]}: command not found")
    return (proc.returncode, proc.stdout, proc.stderr)
