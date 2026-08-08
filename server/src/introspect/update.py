"""Check/preflight/apply for the self-update flow (spec §5).

Thin, honest wrapper around git + update.sh, shared by ``introspect update`` (CLI)
and ``/update`` (TUI). Never stashes, merges, or resets: dirty and diverged states
are reported as user decisions. All git failures raise :class:`UpdateError` whose
message carries the command and the stderr tail -- the surfaces print it verbatim.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from introspect.changelog import (
    ChangelogError,
    Entry,
    current_version,
    entries_newer_than,
    parse_changelog,
)


class UpdateError(RuntimeError):
    """A git invocation failed; message = command + stderr tail."""


class UpdateState(Enum):
    UP_TO_DATE = "up_to_date"
    BEHIND = "behind"
    LOCAL_AHEAD = "local_ahead"
    NO_CHANGELOG = "no_changelog"


@dataclass(frozen=True)
class UpdateCheck:
    state: UpdateState
    local_version: str
    remote_version: str
    new_entries: tuple[Entry, ...]


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    server_changed: bool


def find_repo_root(start: Path | None = None) -> Path | None:
    origin = start if start is not None else Path(__file__).resolve()
    for ancestor in (origin, *origin.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise UpdateError(f"git {' '.join(args)} failed:\n{tail}")
    return proc.stdout.strip()


def _upstream(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def check(repo_root: Path) -> UpdateCheck:
    upstream = _upstream(repo_root)          # e.g. "origin/main"
    remote_name = upstream.split("/", 1)[0]
    _git(repo_root, "fetch", remote_name)

    local_path = repo_root / "CHANGELOG.md"
    if not local_path.is_file():
        return UpdateCheck(UpdateState.NO_CHANGELOG, "", "", ())
    try:
        local_v = current_version(local_path.read_text(encoding="utf-8"))
        remote_entries = parse_changelog(_git(repo_root, "show", f"{upstream}:CHANGELOG.md"))
    except (ChangelogError, UpdateError):
        # A malformed local/remote changelog (or CHANGELOG.md absent on the remote)
        # means versions can't be compared; surfaces tell the user to update manually.
        return UpdateCheck(UpdateState.NO_CHANGELOG, "", "", ())
    remote_v = remote_entries[0].version
    if remote_v == local_v:
        return UpdateCheck(UpdateState.UP_TO_DATE, local_v, remote_v, ())
    newer = entries_newer_than(remote_entries, local_v)
    if newer is None:
        return UpdateCheck(UpdateState.LOCAL_AHEAD, local_v, remote_v, ())
    return UpdateCheck(UpdateState.BEHIND, local_v, remote_v, tuple(newer))


def preflight_problems(repo_root: Path) -> list[str]:
    problems: list[str] = []
    dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        problems.append(
            "working tree has uncommitted changes to tracked files -- commit or stash "
            "them first (update never stashes)"
        )
    counts = _git(repo_root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    _behind, ahead = (int(n) for n in counts.split())
    if ahead > 0:
        problems.append(
            "local branch has commits origin doesn't -- resolve manually (update never merges)"
        )
    return problems


def apply(
    repo_root: Path,
    emit: Callable[[str], None],
    update_script: Path | None = None,
) -> ApplyResult:
    old_head = _git(repo_root, "rev-parse", "HEAD")
    script = update_script if update_script is not None else repo_root / "update.sh"
    proc = subprocess.Popen(
        [str(script)],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None  # PIPE above guarantees it; narrows the type
    for line in proc.stdout:
        emit(line.rstrip("\n"))
    ok = proc.wait() == 0
    new_head = _git(repo_root, "rev-parse", "HEAD")
    server_changed = (
        ok
        and old_head != new_head
        and bool(_git(repo_root, "diff", "--name-only", f"{old_head}..{new_head}", "--", "server/"))
    )
    return ApplyResult(ok=ok, server_changed=server_changed)
