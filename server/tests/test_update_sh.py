"""End-to-end tests for the repo-root ``update.sh`` convergence layer (Task 2).

These run the REAL ``update.sh`` (and, through it, the real ``install.sh``) via ``/bin/bash``
inside a hermetic sandbox built on the same fakebin/sysbin/argv-log architecture as
``test_installer.py`` (see that file's module docstring). ``FULL_FAKES``'s ``uv``/``npm``/``node``
fakes are reused unmodified so the re-converge step exercises the real ``install.sh``; only
``git`` is replaced with a richer fake here, since ``update.sh`` is the first script in this repo
that actually drives git rather than merely checking it is present.

The fake ``git`` is controlled entirely through ``FAKE_GIT_*`` env vars (see the script body
below) and, like every other fake, appends its invocation to ``$FAKE_ARGV_LOG`` -- the ``-C
<repo>`` prefix ``update.sh`` always passes is stripped before logging, so a logged line reads
``git pull --ff-only``, matching what a human would type.

``update.sh`` derives its repo root from ``BASH_SOURCE`` exactly like ``install.sh``, so copying
both scripts into the sandbox makes the sandbox the repo. The sandbox seeds its own
``CHANGELOG.md`` fixture -- these tests never read or depend on the real repo's CHANGELOG.md or
git state.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.test_installer import FULL_FAKES, _make_sysbin, _write_exec

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
UPDATE_SH = REPO_ROOT / "update.sh"

_OLD_CHANGELOG = "# Changelog\n\n## 1.0.0 — 2026-08-01\n\n- initial release\n"
_NEW_CHANGELOG = (
    "# Changelog\n\n## 1.1.0 — 2026-08-08\n\n- new stuff\n\n## 1.0.0 — 2026-08-01\n\n"
    "- initial release\n"
)

# --- Fake git ------------------------------------------------------------------------------
# update.sh always invokes git as `git -C "$REPO_ROOT" <subcommand...>`; the fake strips the
# `-C <repo>` pair (remembering <repo> for the pull side effect) before logging and dispatching,
# so both the argv log and the case patterns below read like a plain git invocation.

FAKE_GIT = """#!/bin/sh
repo_root=""
if [ "$1" = "-C" ]; then
  repo_root="$2"
  shift 2
fi
printf '%s\\n' "git $*" >> "$FAKE_ARGV_LOG"

case "$*" in
  "rev-parse --is-inside-work-tree")
    echo true
    ;;
  "status --porcelain --untracked-files=no")
    printf '%s\\n' "${FAKE_GIT_DIRTY:-}"
    ;;
  "rev-parse --abbrev-ref --symbolic-full-name @{u}")
    if [ "${FAKE_GIT_NO_UPSTREAM:-}" = "1" ]; then
      exit 1
    fi
    echo "origin/main"
    ;;
  "rev-parse HEAD")
    cat "$FAKE_GIT_HEAD_FILE"
    ;;
  "pull --ff-only")
    if [ "${FAKE_GIT_PULL_FAIL:-}" = "1" ]; then
      echo "fatal: Not possible to fast-forward, aborting." >&2
      exit 1
    fi
    if [ "${FAKE_GIT_PULL_ADVANCES:-}" = "1" ]; then
      printf '%s' "bbbb" > "$FAKE_GIT_HEAD_FILE"
      if [ -n "${FAKE_GIT_PULL_CHANGELOG:-}" ]; then
        cat "$FAKE_GIT_PULL_CHANGELOG" > "$repo_root/CHANGELOG.md"
      fi
    fi
    ;;
  *)
    echo "fake git: unhandled invocation: $*" >&2
    exit 1
    ;;
esac
exit 0
"""


# --- Sandbox harness -----------------------------------------------------------------------


@dataclass
class UpdateSandbox:
    root: Path  # the scratch "repo" (holds install.sh + update.sh + CHANGELOG.md + server/ + web/)
    fakebin: Path
    home: Path
    argv_log: Path
    sysbin: Path
    base_env: dict[str, str] = field(default_factory=dict)

    def write_fakes(self, fakes: dict[str, str]) -> None:
        for name, content in fakes.items():
            _write_exec(self.fakebin / name, content)

    def run_update(
        self, env_extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.fakebin}:{self.sysbin}",
            "HOME": str(self.home),
            "FAKE_ARGV_LOG": str(self.argv_log),
            **self.base_env,
        }
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/bash", str(self.root / "update.sh")],
            env=env,
            capture_output=True,
            text=True,
        )

    @property
    def argv(self) -> str:
        return self.argv_log.read_text()

    @property
    def argv_lines(self) -> list[str]:
        return [ln for ln in self.argv_log.read_text().splitlines() if ln.strip()]


def _make_update_sandbox(tmp_path: Path, pull_advances: bool = False) -> UpdateSandbox:
    if not UPDATE_SH.exists():  # RED signal when update.sh does not exist yet
        pytest.fail(f"update.sh not found at {UPDATE_SH}")
    if not INSTALL_SH.exists():
        pytest.fail(f"install.sh not found at {INSTALL_SH}")

    root = tmp_path / "repo"
    root.mkdir()
    (root / "server").mkdir()
    (root / "web").mkdir()
    for src, name in ((INSTALL_SH, "install.sh"), (UPDATE_SH, "update.sh")):
        shutil.copy(src, root / name)
        (root / name).chmod(0o755)
    (root / "CHANGELOG.md").write_text(_OLD_CHANGELOG)

    fakebin = root / "fakebin"
    fakebin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    argv_log = tmp_path / "argv.log"
    argv_log.touch()
    sysbin = _make_sysbin(tmp_path)

    git_head_file = tmp_path / "git_head"
    git_head_file.write_text("aaaa")

    sb = UpdateSandbox(root=root, fakebin=fakebin, home=home, argv_log=argv_log, sysbin=sysbin)
    fakes = dict(FULL_FAKES)
    fakes["git"] = FAKE_GIT
    sb.write_fakes(fakes)

    sb.base_env = {"FAKE_GIT_HEAD_FILE": str(git_head_file)}
    if pull_advances:
        pulled_changelog = tmp_path / "pulled_CHANGELOG.md"
        pulled_changelog.write_text(_NEW_CHANGELOG)
        sb.base_env["FAKE_GIT_PULL_ADVANCES"] = "1"
        sb.base_env["FAKE_GIT_PULL_CHANGELOG"] = str(pulled_changelog)
    return sb


# --- Tests -----------------------------------------------------------------------------------


def test_happy_path_pulls_then_reconverges_without_import(tmp_path: Path) -> None:
    sb = _make_update_sandbox(tmp_path, pull_advances=True)
    proc = sb.run_update()
    assert proc.returncode == 0, proc.stderr
    joined = sb.argv_lines
    # git preflights happen before the pull; pull before install.sh's tools
    assert "git pull --ff-only" in joined
    assert joined.index("git pull --ff-only") < joined.index("uv sync")
    assert "uv run introspect import" not in joined  # --skip-import
    assert "updated 1.0.0 -> 1.1.0" in proc.stdout


def test_already_up_to_date_reports_and_exits_zero(tmp_path: Path) -> None:
    sb = _make_update_sandbox(tmp_path, pull_advances=False)
    proc = sb.run_update()
    assert proc.returncode == 0
    assert "already up to date (1.0.0)" in proc.stdout
    # convergence still ran: a re-run after a pull that changed nothing must still repair
    assert "uv sync" in sb.argv_lines


def test_dirty_tree_aborts_before_pulling(tmp_path: Path) -> None:
    sb = _make_update_sandbox(tmp_path)
    proc = sb.run_update(env_extra={"FAKE_GIT_DIRTY": " M server/x.py"})
    assert proc.returncode != 0
    assert "uncommitted changes" in proc.stdout + proc.stderr
    assert "git pull --ff-only" not in sb.argv_lines
    assert "never stashes" in proc.stdout + proc.stderr


def test_no_upstream_aborts_with_guidance(tmp_path: Path) -> None:
    sb = _make_update_sandbox(tmp_path)
    proc = sb.run_update(env_extra={"FAKE_GIT_NO_UPSTREAM": "1"})
    assert proc.returncode != 0
    assert "upstream" in (proc.stdout + proc.stderr)


def test_pull_failure_is_honest_and_nonzero(tmp_path: Path) -> None:
    sb = _make_update_sandbox(tmp_path)
    proc = sb.run_update(env_extra={"FAKE_GIT_PULL_FAIL": "1"})
    assert proc.returncode != 0
    assert "never merges" in (proc.stdout + proc.stderr)
    assert "uv sync" not in sb.argv_lines  # install.sh never ran
