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
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from introspect.cron import Runner
from introspect.export import SessionNotFoundError, export_session_to
from introspect.models import ChatSession, Project

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


def resume_session(
    db: Session,
    session_uuid: str,
    *,
    source_root: Path,
    terminal_app: str,
    scripts_dir: Path,
    runner: Runner | None = None,
) -> ResumeOutcome:
    """Presence-check → restore-if-missing → launch. Never overwrites an existing live file
    (§17.1.2 — it may be ahead of the archive). Raises SessionNotFoundError for unknown sessions;
    everything after that point is an OUTCOME, not an exception (§17.2)."""
    run = runner or _subprocess_runner
    session = db.get(ChatSession, session_uuid)
    if session is None:
        raise SessionNotFoundError(session_uuid)
    project = db.get(Project, session.project_id)
    live_path = source_root / project.dir_slug / f"{session_uuid}.jsonl"
    command = build_resume_command(session_uuid)

    restored = False
    if not live_path.exists():
        live_path.parent.mkdir(parents=True, exist_ok=True)
        export_session_to(db, session_uuid, live_path)
        restored = True

    cwd = project.resolved_cwd
    if cwd is None or not Path(cwd).is_dir():
        return ResumeOutcome(
            restored=restored,
            launched=False,
            mode=MODE_MISSING_CWD,
            command=command,
            cwd=cwd,
            live_path=str(live_path),
            detail=cwd or "no working directory recorded for this session's project",
        )

    if sys.platform != "darwin":
        return ResumeOutcome(
            restored=restored,
            launched=False,
            mode=MODE_UNSUPPORTED,
            command=command,
            cwd=cwd,
            live_path=str(live_path),
            detail=None,
        )

    scripts_dir.mkdir(parents=True, exist_ok=True)
    # NOTE(claude): session_uuid reaches the filename only after db.get(ChatSession, ...)
    # succeeds above -- only uuids that exist as primary keys proceed, so no path traversal
    # via crafted ids.
    script_path = scripts_dir / f"{session_uuid}.command"
    script_path.write_text(build_launch_script(cwd, session_uuid))
    script_path.chmod(0o755)

    code, _out, err = run(["open", "-a", terminal_app, str(script_path)], None)
    if code != 0:
        return ResumeOutcome(
            restored=restored,
            launched=False,
            mode=MODE_OPEN_FAILED,
            command=command,
            cwd=cwd,
            live_path=str(live_path),
            detail=err.strip() or f"open exited {code}",
        )
    return ResumeOutcome(
        restored=restored,
        launched=True,
        mode=MODE_LAUNCHED,
        command=command,
        cwd=cwd,
        live_path=str(live_path),
        detail=None,
    )
