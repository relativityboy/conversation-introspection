"""End-to-end tests for the repo-root ``install.sh`` orchestrator (Task P4-F10).

These run the REAL ``install.sh`` via ``/bin/bash`` inside a hermetic sandbox:

* a scratch ``$HOME`` (so the resolved archive path is under the sandbox, never the real one);
* a ``fakebin/`` dir of scripted fakes for ``uv``/``npm``/``node``/``git``/``curl`` that log
  their argv to a shared file and simulate each real tool's side effect (uv sync -> ``.venv``,
  npm ci -> ``node_modules``, npm run build -> ``dist/index.html``, import -> the archive db);
* a ``sysbin/`` of symlinks to only the coreutils the script needs, so the tool under test's
  PATH contains *exactly* the fakes plus plain unix utilities -- ``node``/``uv`` are present iff
  a fake is provided, which is what makes the "missing node" and "uv absent" scenarios exact.

``install.sh`` derives its repo root from ``BASH_SOURCE``, so copying it into the sandbox makes
the sandbox the repo -- and its build artifacts (``server/.venv`` etc.) do not pre-exist, so the
happy-path run actually executes every step. The argv log is the assertion surface: it records
which tools ran, in what order, and (across two runs) whether completed steps re-execute.

No new dependencies: this is the red/green pattern for shell -- pytest drives the subprocess.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"

# Only these coreutils are exposed to the script under test (plus the fakes). Anything not here
# and not faked -- notably node/npm/uv/git/curl -- is simply absent, by construction.
_SYS_TOOLS = [
    "sh", "bash", "uname", "mktemp", "tee", "tail", "head", "sed", "rm", "cat", "mkdir",
    "chmod", "touch", "cut", "tr", "grep", "dirname", "ls", "env", "printf", "sleep",
]

# --- Fake tool scripts --------------------------------------------------------------------
# Each fake appends "<name> <argv>" to $FAKE_ARGV_LOG, then optionally fails (when its
# FAKE_*_FAIL_ON env matches the joined argv) or simulates the real tool's on-disk side effect.

FAKE_UV = """#!/bin/sh
printf '%s\\n' "uv $*" >> "$FAKE_ARGV_LOG"
if [ -n "${FAKE_UV_FAIL_ON:-}" ] && [ "$*" = "$FAKE_UV_FAIL_ON" ]; then
  echo "uv: simulated failure for '$*'" >&2
  exit 1
fi
if [ "$1" = "sync" ]; then mkdir -p .venv; fi
if [ "$1" = "run" ] && [ "$2" = "introspect" ] && [ "$3" = "import" ]; then
  mkdir -p "$HOME/.conversation-introspection"
  : > "$HOME/.conversation-introspection/archive.db"
  echo "imported files=0 records=0 dupes=0 anomalies=0 gone=0 status=ok"
fi
exit 0
"""

FAKE_NPM = """#!/bin/sh
printf '%s\\n' "npm $*" >> "$FAKE_ARGV_LOG"
if [ -n "${FAKE_NPM_FAIL_ON:-}" ] && [ "$*" = "$FAKE_NPM_FAIL_ON" ]; then
  echo "npm ERR! simulated failure for 'npm $*'" >&2
  exit 1
fi
if [ "$*" = "ci" ]; then mkdir -p node_modules; fi
if [ "$*" = "run build" ]; then mkdir -p dist; : > dist/index.html; fi
exit 0
"""

# A bare present-stub: exists (so `command -v` finds it) and never fails. Used for node/git and,
# by default, curl.
FAKE_STUB = """#!/bin/sh
printf '%s\\n' "$(basename "$0") $*" >> "$FAKE_ARGV_LOG"
exit 0
"""

# A curl that emits (to stdout) the uv official-installer script, so `curl ... | sh` drops a
# working fake uv into ~/.local/bin -- exercising the consent-gated uv install path.
FAKE_CURL_INSTALLS_UV = """#!/bin/sh
printf '%s\\n' "curl $*" >> "$FAKE_ARGV_LOG"
cat <<'INSTALLER'
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/uv" <<'UVEOF'
#!/bin/sh
printf '%s\\n' "uv $*" >> "$FAKE_ARGV_LOG"
if [ "$1" = "sync" ]; then mkdir -p .venv; fi
if [ "$1" = "run" ] && [ "$2" = "introspect" ] && [ "$3" = "import" ]; then
  mkdir -p "$HOME/.conversation-introspection"
  : > "$HOME/.conversation-introspection/archive.db"
  echo "imported files=0 status=ok"
fi
exit 0
UVEOF
chmod +x "$HOME/.local/bin/uv"
INSTALLER
exit 0
"""

# The full set of fakes for a healthy run (uv present).
FULL_FAKES = {"uv": FAKE_UV, "npm": FAKE_NPM, "node": FAKE_STUB, "git": FAKE_STUB, "curl": FAKE_STUB}


# --- Sandbox harness ----------------------------------------------------------------------


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _make_sysbin(root: Path) -> Path:
    sysbin = root / "sysbin"
    sysbin.mkdir()
    for tool in _SYS_TOOLS:
        real = shutil.which(tool)
        if real:
            os.symlink(real, sysbin / tool)
    return sysbin


@dataclass
class Sandbox:
    root: Path  # the scratch "repo" (holds install.sh + server/ + web/)
    fakebin: Path
    home: Path
    argv_log: Path
    sysbin: Path

    def write_fakes(self, fakes: dict[str, str]) -> None:
        for name, content in fakes.items():
            _write_exec(self.fakebin / name, content)

    def truncate_argv(self) -> None:
        self.argv_log.write_text("")

    def run(
        self,
        *flags: str,
        stdin: str | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.fakebin}:{self.sysbin}",
            "HOME": str(self.home),
            "FAKE_ARGV_LOG": str(self.argv_log),
        }
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/bash", str(self.root / "install.sh"), *flags],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
        )

    @property
    def argv(self) -> str:
        return self.argv_log.read_text()

    @property
    def argv_lines(self) -> list[str]:
        return [ln for ln in self.argv_log.read_text().splitlines() if ln.strip()]


def _make_sandbox(tmp_path: Path, fakes: dict[str, str] | None = None) -> Sandbox:
    if not INSTALL_SH.exists():  # RED signal when install.sh does not exist yet
        pytest.fail(f"install.sh not found at {INSTALL_SH}")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "server").mkdir()
    (root / "web").mkdir()
    shutil.copy(INSTALL_SH, root / "install.sh")
    (root / "install.sh").chmod(0o755)
    fakebin = root / "fakebin"
    fakebin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    argv_log = tmp_path / "argv.log"
    argv_log.touch()
    sysbin = _make_sysbin(tmp_path)
    sb = Sandbox(root=root, fakebin=fakebin, home=home, argv_log=argv_log, sysbin=sysbin)
    sb.write_fakes(FULL_FAKES if fakes is None else fakes)
    return sb


_WORK_STEPS = ["uv sync", "npm ci", "npm run build", "uv run introspect import"]


def _work_steps_in(argv: str) -> list[str]:
    return [step for step in _WORK_STEPS if step in argv]


# --- Tests --------------------------------------------------------------------------------


def test_happy_path_runs_the_right_tools_in_order(tmp_path: Path) -> None:
    sb = _make_sandbox(tmp_path)
    proc = sb.run("--yes")
    assert proc.returncode == 0, proc.stderr
    # Exactly the four work steps, in dependency order.
    assert sb.argv_lines == ["uv sync", "npm ci", "npm run build", "uv run introspect import"]
    # Artifacts the fakes produced prove each step actually reached its tool.
    assert (sb.root / "server" / ".venv").is_dir()
    assert (sb.root / "web" / "node_modules").is_dir()
    assert (sb.root / "web" / "dist" / "index.html").is_file()
    assert (sb.home / ".conversation-introspection" / "archive.db").is_file()


def test_skip_import_flag_omits_import(tmp_path: Path) -> None:
    sb = _make_sandbox(tmp_path)
    proc = sb.run("--yes", "--skip-import")
    assert proc.returncode == 0, proc.stderr
    assert "uv run introspect import" not in sb.argv
    assert not (sb.home / ".conversation-introspection" / "archive.db").exists()
    assert sb.argv_lines == ["uv sync", "npm ci", "npm run build"]


def test_missing_node_fails_clean_with_hint_and_nothing_half_done(tmp_path: Path) -> None:
    # No `node` fake -> node is absent from PATH entirely.
    fakes = {k: v for k, v in FULL_FAKES.items() if k != "node"}
    sb = _make_sandbox(tmp_path, fakes=fakes)
    proc = sb.run("--yes")
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "node" in combined.lower()
    assert "nodejs.org" in combined or "install" in combined.lower()
    # Preflight fails before any step runs: nothing half-done.
    assert sb.argv_lines == []
    assert not (sb.root / "server" / ".venv").exists()
    assert not (sb.root / "web" / "node_modules").exists()


def test_yes_accepts_uv_install_without_prompting(tmp_path: Path) -> None:
    # uv absent; curl emits the official installer. --yes must accept the uv install
    # non-interactively (no stdin), then the run proceeds to completion.
    fakes = {"npm": FAKE_NPM, "node": FAKE_STUB, "git": FAKE_STUB, "curl": FAKE_CURL_INSTALLS_UV}
    sb = _make_sandbox(tmp_path, fakes=fakes)
    proc = sb.run("--yes", stdin="")  # empty stdin: must NOT block on a prompt
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "astral.sh/uv/install.sh" in sb.argv  # the official installer was fetched
    # After installing uv, the normal steps ran.
    assert _work_steps_in(sb.argv) == _WORK_STEPS


def test_uv_missing_prompt_declined_aborts_without_changes(tmp_path: Path) -> None:
    fakes = {"npm": FAKE_NPM, "node": FAKE_STUB, "git": FAKE_STUB, "curl": FAKE_CURL_INSTALLS_UV}
    sb = _make_sandbox(tmp_path, fakes=fakes)
    proc = sb.run(stdin="n\n")  # no --yes: a prompt must appear; decline it
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "uv" in combined.lower()
    # Declining aborts before any build step.
    assert "uv sync" not in sb.argv
    assert not (sb.root / "server" / ".venv").exists()


def test_idempotent_second_run_skips_completed_steps(tmp_path: Path) -> None:
    sb = _make_sandbox(tmp_path)
    first = sb.run("--yes")
    assert first.returncode == 0, first.stderr
    sb.truncate_argv()
    second = sb.run("--yes")
    assert second.returncode == 0, second.stderr
    # No completed work step re-executes on the second run.
    assert _work_steps_in(sb.argv) == []
    # And the user is told each was already done.
    assert second.stdout.lower().count("already") >= 3


def test_midstep_failure_names_the_step_and_exits_nonzero(tmp_path: Path) -> None:
    sb = _make_sandbox(tmp_path)
    proc = sb.run("--yes", env_extra={"FAKE_NPM_FAIL_ON": "run build"})
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    # The failing step is named, its captured stderr tail is shown, and re-run guidance given.
    assert "npm run build" in combined or "web UI" in combined.lower()
    assert "simulated failure" in combined  # the captured tool stderr tail
    assert "re-run" in combined.lower()
    # uv sync completed before the failure; the build artifact was never produced.
    assert (sb.root / "server" / ".venv").is_dir()
    assert not (sb.root / "web" / "dist" / "index.html").exists()


def test_rerun_after_failure_resumes_at_failed_step(tmp_path: Path) -> None:
    sb = _make_sandbox(tmp_path)
    first = sb.run("--yes", env_extra={"FAKE_NPM_FAIL_ON": "run build"})
    assert first.returncode != 0
    # Fix the environment (npm no longer fails) and re-run.
    sb.truncate_argv()
    second = sb.run("--yes")
    assert second.returncode == 0, second.stdout + second.stderr
    steps = sb.argv_lines
    # Completed steps are skipped; the run resumes at the previously-failed build step.
    assert "uv sync" not in steps
    assert "npm ci" not in steps
    assert "npm run build" in steps
    assert "uv run introspect import" in steps
    assert (sb.root / "web" / "dist" / "index.html").is_file()
