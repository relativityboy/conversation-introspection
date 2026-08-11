# Versions and `/update` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make currency a first-class property: a curated `CHANGELOG.md` drives version display in the web UI and TUI, `/update` (TUI) and `introspect update` (CLI) check/describe/apply updates via a new `update.sh`, and cache headers stop stale bundles hiding in the browser.

**Architecture:** `CHANGELOG.md` at repo root is the single source of truth (top entry = current version). A dependency-free parser (`introspect/changelog.py`) feeds every surface. `update.sh` is the promptless convergence layer (`git pull --ff-only` + `./install.sh --yes --skip-import`); `introspect/update.py` wraps check/preflight/apply for both the CLI and TUI. The web bundle bakes its version in at build time so a stale bundle self-reports; the StatusBar shows ui-vs-server mismatch. Spec: `docs/superpowers/specs/2026-08-08-versions-and-update-design.md`.

**Tech Stack:** Python 3.12 / argparse / FastAPI / Textual / pytest; bash (orchestrator scripts); React + vite + vitest.

## Global Constraints

- **Zero-legacy pre-release law:** delete, don't deprecate. No aliases, no back-compat shims.
- **Commits:** each task commits its own work: `git add <files> && git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "<terse subject>"`. Terse subject only — no body, no Co-Authored-By trailers. Never `git add -A`; never stage anything under `claude_notes/`.
- **Never touch** `server/pyproject.toml`'s `version` or `introspect/__init__.py`'s `__version__` — package metadata, not the release version.
- **Changelog grammar:** entries start `## <MAJOR.MINOR.PATCH> — <YYYY-MM-DD>` (em-dash canonical, plain hyphen accepted); bullets are `- ` lines; prose before the first `## ` is ignored.
- **`update.sh` is promptless by design** — consent lives in its callers.
- **Tests must never depend on the real repo's `CHANGELOG.md`** — inject versions/paths in every test.
- Server commands run from `server/` via `uv run`; web commands from `web/` via npm.
- **If a permission is denied, stop and report** — never work around a denial.

---

### Task 1: Changelog parser (`introspect/changelog.py`)

**Files:**
- Create: `server/src/introspect/changelog.py`
- Test: `server/tests/test_changelog.py`

**Interfaces:**
- Produces: `Entry(version: str, date: str, bullets: tuple[str, ...])` (frozen dataclass); `ChangelogError(ValueError)`; `parse_changelog(text: str) -> list[Entry]` (raises `ChangelogError` on malformed/empty); `current_version(text: str) -> str`; `entries_newer_than(entries: list[Entry], version: str) -> list[Entry] | None` (prefix of entries strictly newer than `version`; `None` if `version` absent from the list); `find_changelog(start: Path) -> Path | None` (walk-up, `.git` boundary); `app_version(start: Path | None = None) -> str` (best-effort: `"unknown"` on missing/malformed; default start = the module's own path).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_changelog.py
"""Changelog parsing: CHANGELOG.md is the single source of truth for the release version."""

from pathlib import Path

import pytest

from introspect.changelog import (
    ChangelogError,
    Entry,
    app_version,
    current_version,
    entries_newer_than,
    find_changelog,
    parse_changelog,
)

WELL_FORMED = """\
# Changelog

Prose preamble is ignored.

## 1.2.0 — 2026-08-08
- Versions everywhere.
- `/update` in the TUI.

## 1.1.0 - 2026-08-07
- Authorship labels.

## 1.0.0 — 2026-08-01
- V1.
"""


def test_parses_entries_in_order_with_bullets() -> None:
    entries = parse_changelog(WELL_FORMED)
    assert [e.version for e in entries] == ["1.2.0", "1.1.0", "1.0.0"]
    assert entries[0] == Entry(
        version="1.2.0",
        date="2026-08-08",
        bullets=("Versions everywhere.", "`/update` in the TUI."),
    )
    # hyphen-separated heading (1.1.0) parses the same as em-dash
    assert entries[1].date == "2026-08-07"


def test_current_version_is_top_entry() -> None:
    assert current_version(WELL_FORMED) == "1.2.0"


def test_malformed_heading_raises() -> None:
    with pytest.raises(ChangelogError):
        parse_changelog("## not-a-version — 2026-08-08\n- x\n")


def test_no_entries_raises() -> None:
    with pytest.raises(ChangelogError):
        parse_changelog("# Changelog\n\njust prose\n")
    with pytest.raises(ChangelogError):
        parse_changelog("")


def test_entries_newer_than() -> None:
    entries = parse_changelog(WELL_FORMED)
    newer = entries_newer_than(entries, "1.0.0")
    assert newer is not None
    assert [e.version for e in newer] == ["1.2.0", "1.1.0"]
    assert entries_newer_than(entries, "1.2.0") == []
    assert entries_newer_than(entries, "0.9.0") is None  # unknown local version


def test_find_changelog_walks_up_and_stops_at_git_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")
    deep = repo / "server" / "src" / "introspect"
    deep.mkdir(parents=True)
    assert find_changelog(deep) == repo / "CHANGELOG.md"

    # no changelog anywhere inside the repo boundary -> None (never ascends past .git)
    bare = tmp_path / "bare"
    (bare / ".git").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")  # OUTSIDE the repo
    inner = bare / "server"
    inner.mkdir()
    assert find_changelog(inner) is None


def test_app_version_best_effort(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CHANGELOG.md").write_text(WELL_FORMED, encoding="utf-8")
    assert app_version(repo / "server") == "1.2.0"

    (repo / "CHANGELOG.md").write_text("garbage\n", encoding="utf-8")
    assert app_version(repo / "server") == "unknown"

    nowhere = tmp_path / "nowhere"
    (nowhere / ".git").mkdir(parents=True)
    assert app_version(nowhere) == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/test_changelog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'introspect.changelog'`

- [ ] **Step 3: Implement `changelog.py`**

```python
# server/src/introspect/changelog.py
"""CHANGELOG.md as the single source of truth for the app's release version.

The top entry IS the current version (no git tags; nothing else to keep in sync).
Grammar: an entry heading is ``## <MAJOR.MINOR.PATCH> — <YYYY-MM-DD>`` (em-dash
canonical, plain hyphen accepted); ``- `` lines beneath it are that version's
user-facing changelist; prose before the first heading is ignored.

The parser RAISES on malformed input; runtime surfaces call :func:`app_version`,
which degrades to ``"unknown"`` instead — a broken changelog must never take down
serving or updating, but it must not pass silently either (each surface prints a
visible note when it sees ``"unknown"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) [—-] (\d{4}-\d{2}-\d{2})\s*$")


class ChangelogError(ValueError):
    """CHANGELOG.md content that violates the entry grammar."""


@dataclass(frozen=True)
class Entry:
    version: str
    date: str
    bullets: tuple[str, ...]


def parse_changelog(text: str) -> list[Entry]:
    entries: list[Entry] = []
    current: tuple[str, str] | None = None
    bullets: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            if current is not None:
                entries.append(Entry(current[0], current[1], tuple(bullets)))
            current = (match.group(1), match.group(2))
            bullets = []
        elif line.startswith("## "):
            raise ChangelogError(f"malformed changelog heading: {line!r}")
        elif current is not None and line.startswith("- "):
            bullets.append(line[2:].strip())
    if current is not None:
        entries.append(Entry(current[0], current[1], tuple(bullets)))
    if not entries:
        raise ChangelogError("no release entries found")
    return entries


def current_version(text: str) -> str:
    return parse_changelog(text)[0].version


def entries_newer_than(entries: list[Entry], version: str) -> list[Entry] | None:
    """Entries strictly newer than ``version`` (list order = newest first).

    Returns ``None`` when ``version`` does not appear at all — the caller cannot
    know what "newer" means then (e.g. a local checkout ahead of origin).
    """
    for i, entry in enumerate(entries):
        if entry.version == version:
            return entries[:i]
    return None


def find_changelog(start: Path) -> Path | None:
    """Walk from ``start`` up its ancestors for CHANGELOG.md.

    Same boundary rule as ``_walk_up_for_ui_dist`` (api/__init__.py): the first
    ancestor containing ``.git`` is still checked, but the walk never ascends
    past it, so an unrelated CHANGELOG.md above the repo is never picked up.
    """
    for ancestor in (start, *start.parents):
        candidate = ancestor / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
        if (ancestor / ".git").exists():
            return None
    return None


def app_version(start: Path | None = None) -> str:
    """Best-effort current version for runtime surfaces; ``"unknown"`` on any failure."""
    origin = start if start is not None else Path(__file__).resolve()
    path = find_changelog(origin)
    if path is None:
        return "unknown"
    try:
        return current_version(path.read_text(encoding="utf-8"))
    except (OSError, ChangelogError):
        return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/test_changelog.py -q`
Expected: PASS (all)

- [ ] **Step 5: Lint and commit**

Run: `cd server && uv run ruff check src/introspect/changelog.py tests/test_changelog.py`

```bash
git add server/src/introspect/changelog.py server/tests/test_changelog.py
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "server: changelog parser -- CHANGELOG.md as version source of truth (spec §2)"
```

---

### Task 2: `update.sh` — the convergence layer

**Files:**
- Create: `update.sh` (repo root; `chmod +x`)
- Modify: `server/tests/test_installer.py` (add a fake `git` to the shared fakes if simpler) — otherwise leave untouched
- Test: `server/tests/test_update_sh.py`

**Interfaces:**
- Consumes: `./install.sh --yes --skip-import` (exists; always-run orchestrator).
- Produces: `./update.sh` — promptless; exit 0 on success (including already-up-to-date), nonzero with an honest message otherwise. Prints `updated <old> -> <new>` or `already up to date (<version>)`.

- [ ] **Step 1: Write the failing tests**

Follow `server/tests/test_installer.py`'s sandbox pattern exactly (`_make_sandbox`-style: scratch `$HOME`, `fakebin/` of argv-logging fakes, `sysbin/` symlinks). Copy BOTH `install.sh` and `update.sh` into the sandbox repo. Add a fake `git` (same `FAKE_*` recipe as the existing fakes) with these behaviors, controlled by env vars:

- `rev-parse --is-inside-work-tree` → prints `true`, exit 0
- `status --porcelain --untracked-files=no` → prints `$FAKE_GIT_DIRTY` (default empty)
- `rev-parse --abbrev-ref --symbolic-full-name @{u}` → prints `origin/main`, or exits 1 when `FAKE_GIT_NO_UPSTREAM=1`
- `rev-parse HEAD` → prints the contents of `$FAKE_GIT_HEAD_FILE` (the sandbox seeds it with `aaaa`)
- `pull --ff-only` → exits 1 with `fatal: Not possible to fast-forward` on stderr when `FAKE_GIT_PULL_FAIL=1`; otherwise, if `FAKE_GIT_PULL_ADVANCES=1`, writes `bbbb` to `$FAKE_GIT_HEAD_FILE` and, if `$FAKE_GIT_PULL_CHANGELOG` is set, copies that file over `repo/CHANGELOG.md`
- every invocation appends `git <args>` to `$FAKE_ARGV_LOG`

Sandbox repo additionally gets a `CHANGELOG.md` containing a `## 1.0.0 — 2026-08-01` entry, and the test writes a "pulled" changelog with `## 1.1.0 — 2026-08-08` on top for the advance case.

```python
# server/tests/test_update_sh.py — representative cases (write all of these)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/test_update_sh.py -q`
Expected: FAIL — update.sh does not exist yet

- [ ] **Step 3: Write `update.sh`**

```bash
#!/usr/bin/env bash
#
# update.sh -- pull the latest release and re-converge (deps, web build). PROMPTLESS BY
# DESIGN: consent lives in the callers (`/update`'s confirm in the TUI, `introspect
# update`'s [y/N], or you deciding to run this). Like install.sh it is an ORCHESTRATOR:
# git and install.sh do the work; this script sequences them and reports honestly.
#
# It never stashes, merges, or resets. A dirty tree or a diverged branch is YOUR
# decision; this script stops and says exactly what it found.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -t 1 ]; then
  C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_BOLD=''; C_RED=''; C_OFF=''
fi
log_step() { printf '%s==>%s %s\n' "$C_BOLD" "$C_OFF" "$*"; }
log_err()  { printf '%serror%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }

current_version() {
  sed -n 's/^## \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)[[:space:]].*/\1/p' \
    "$REPO_ROOT/CHANGELOG.md" 2>/dev/null | head -n 1
}

log_step "Preflight"
if ! command -v git >/dev/null 2>&1; then
  log_err "git is required."; exit 1
fi
if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log_err "not a git checkout: $REPO_ROOT -- update.sh only works from a cloned repo."; exit 1
fi
dirty="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
  log_err "working tree has uncommitted changes to tracked files:"
  printf '%s\n' "$dirty" >&2
  log_err "commit or stash them yourself, then re-run -- update.sh never stashes."
  exit 1
fi
if ! upstream="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
  log_err "the current branch has no upstream -- set one (git branch --set-upstream-to=...) or pull manually."
  exit 1
fi

old_version="$(current_version)"
old_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"

log_step "Pull ($upstream, fast-forward only)"
if ! git -C "$REPO_ROOT" pull --ff-only; then
  log_err "pull failed. If the branch has diverged from $upstream, resolve it yourself -- update.sh never merges."
  exit 1
fi

log_step "Re-converge (./install.sh --yes --skip-import)"
if ! "$REPO_ROOT/install.sh" --yes --skip-import; then
  # install.sh already printed which step failed and why.
  log_err "update incomplete -- fix the problem above and re-run ./update.sh (every step re-converges)."
  exit 1
fi

new_version="$(current_version)"
new_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [ "$old_head" = "$new_head" ]; then
  log_step "already up to date (${new_version:-unknown})"
else
  log_step "updated ${old_version:-unknown} -> ${new_version:-unknown}"
fi
```

Then: `chmod +x update.sh`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/test_update_sh.py -q`
Expected: PASS. Also run `uv run pytest tests/test_installer.py -q` — must stay green.

- [ ] **Step 5: Commit**

```bash
git add update.sh server/tests/test_update_sh.py
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "update.sh: promptless pull + re-converge orchestrator (spec §4)"
```

---

### Task 3: Server-side version surface (`create_app` + `StatusOut.version`)

**Files:**
- Modify: `server/src/introspect/api/__init__.py` (create_app signature + state)
- Modify: `server/src/introspect/api/routes/admin.py` (StatusOut + get_status)
- Test: `server/tests/test_api_admin.py` (or the existing admin-router test file — find it with `grep -l "StatusOut\|/api/v1/status" server/tests/`)

**Interfaces:**
- Consumes: `introspect.changelog.app_version()` (Task 1).
- Produces: `create_app(..., app_version: str | None = None)` → `app.state.app_version: str` (None arg → `changelog.app_version()`); `StatusOut.version: str`; `GET /api/v1/status` returns `version`.

- [ ] **Step 1: Write the failing tests**

In the admin-router test file, find how the test app is built (a `create_app(...)` fixture). Add:

```python
def test_status_reports_injected_app_version(...) -> None:
    # build the app with create_app(..., app_version="9.9.9") using the file's existing fixture pattern
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert resp.json()["version"] == "9.9.9"


def test_app_version_defaults_to_changelog_lookup(...) -> None:
    # create_app with app_version=None must consult introspect.changelog.app_version;
    # monkeypatch it to return "7.7.7" BEFORE building the app:
    monkeypatch.setattr("introspect.api.changelog.app_version", lambda: "7.7.7")
    # ...build app with the fixture pattern, no app_version arg...
    assert client.get("/api/v1/status").json()["version"] == "7.7.7"
```

(Adapt fixture plumbing to the file's local style; every other test in that file shows it. If the existing app fixture is module-scoped, give the second test its own locally-built app.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/ -k "app_version" -q`
Expected: FAIL — `create_app` has no `app_version` param / `version` missing from response

- [ ] **Step 3: Implement**

In `api/__init__.py`:

```python
from introspect import changelog, config
```

Add `app_version: str | None = None` to `create_app`'s signature, and after the other `resolved_*` lines:

```python
resolved_app_version = app_version if app_version is not None else changelog.app_version()
```

and next to the other `app.state.*` assignments:

```python
app.state.app_version = resolved_app_version
```

In `admin.py`, `StatusOut` gains a field and `get_status` returns it:

```python
class StatusOut(BaseModel):
    version: str
    sessions: int
    files: int
    records: int
    archive_bytes: int
    anomalies: AnomalyBreakdown
    last_run: ImportRunOut | None
```

```python
    return StatusOut(
        version=request.app.state.app_version,
        ...existing fields unchanged...
    )
```

- [ ] **Step 4: Run the server suite**

Run: `cd server && uv run pytest -q`
Expected: PASS. If other fixtures construct `StatusOut` directly, add the `version` field there — no aliasing, no defaults.

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/api/__init__.py server/src/introspect/api/routes/admin.py server/tests/<admin test file>
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "api: StatusOut.version from app.state.app_version (spec §3)"
```

---

### Task 4: Update flow core (`introspect/update.py`)

**Files:**
- Create: `server/src/introspect/update.py`
- Test: `server/tests/test_update.py`

**Interfaces:**
- Consumes: `introspect.changelog` (Task 1): `parse_changelog`, `current_version`, `entries_newer_than`, `Entry`.
- Produces:
  - `UpdateError(RuntimeError)` — message carries the git command + stderr tail.
  - `UpdateState` enum: `UP_TO_DATE`, `BEHIND`, `LOCAL_AHEAD`, `NO_CHANGELOG`.
  - `UpdateCheck(state: UpdateState, local_version: str, remote_version: str, new_entries: tuple[Entry, ...])` (frozen dataclass; versions are `""` when not applicable).
  - `ApplyResult(ok: bool, server_changed: bool)` (frozen dataclass).
  - `find_repo_root(start: Path | None = None) -> Path | None` — walk up to the first `.git` ancestor (default start = module path).
  - `check(repo_root: Path) -> UpdateCheck` — fetches, compares local vs `origin`-side changelog.
  - `preflight_problems(repo_root: Path) -> list[str]` — dirty tracked files / local-ahead commits, as user-facing strings; empty = go.
  - `apply(repo_root: Path, emit: Callable[[str], None], update_script: Path | None = None) -> ApplyResult` — streams `update.sh` output line-by-line through `emit`.

- [ ] **Step 1: Write the failing tests**

Use REAL git against tmp_path fixtures (first use of this pattern in the codebase — keep the helper local to this file):

```python
# server/tests/test_update.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/test_update.py -q`
Expected: FAIL — `No module named 'introspect.update'`

- [ ] **Step 3: Implement `update.py`**

```python
# server/src/introspect/update.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/test_update.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

Run: `cd server && uv run ruff check src/introspect/update.py tests/test_update.py`

```bash
git add server/src/introspect/update.py server/tests/test_update.py
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "server: update check/preflight/apply core over git + update.sh (spec §5)"
```

---

### Task 5: `introspect update` (CLI)

**Files:**
- Modify: `server/src/introspect/cli.py`
- Test: `server/tests/test_cli.py` (follow its existing monkeypatch style)

**Interfaces:**
- Consumes (Task 4): `update.find_repo_root()`, `update.check()`, `update.preflight_problems()`, `update.apply()`, `UpdateState`, `UpdateCheck`, `ApplyResult`, `UpdateError`.
- Produces: `introspect update [--yes]` subcommand. Exit 0 = up-to-date or updated; 1 = blocked/failed/declined-by-problems; interactive `[y/N]` unless `--yes`; "n" exits 0.

- [ ] **Step 1: Write the failing tests**

In `test_cli.py`'s style (invoke `main(argv)` or the parser with monkeypatched handlers — copy the file's local pattern), monkeypatching `introspect.cli.update` members:

```python
def test_update_up_to_date(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.update, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        cli.update, "check",
        lambda root: update_mod.UpdateCheck(update_mod.UpdateState.UP_TO_DATE, "1.1.0", "1.1.0", ()),
    )
    assert cli.main(["update"]) == 0
    assert "already up to date (1.1.0)" in capsys.readouterr().out


def test_update_behind_yes_applies_and_reports_restart(monkeypatch, capsys) -> None:
    entry = Entry(version="1.2.0", date="2026-08-08", bullets=("New thing.",))
    monkeypatch.setattr(cli.update, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        cli.update, "check",
        lambda root: update_mod.UpdateCheck(update_mod.UpdateState.BEHIND, "1.1.0", "1.2.0", (entry,)),
    )
    monkeypatch.setattr(cli.update, "preflight_problems", lambda root: [])
    monkeypatch.setattr(
        cli.update, "apply",
        lambda root, emit, update_script=None: update_mod.ApplyResult(ok=True, server_changed=True),
    )
    assert cli.main(["update", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "1.2.0" in out and "New thing." in out
    assert "restart" in out  # server changed -> restart reminder


def test_update_behind_prompt_declined(monkeypatch, capsys) -> None:
    # same check/find_repo_root monkeypatches as above, no --yes;
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.main(["update"]) == 0
    assert "not updating" in capsys.readouterr().out


def test_update_preflight_problems_block(monkeypatch, capsys) -> None:
    # BEHIND check, but preflight_problems returns ["working tree has uncommitted..."]
    # -> exit 1, problems printed, apply never called (monkeypatch apply to raise AssertionError)


def test_update_outside_repo(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.update, "find_repo_root", lambda: None)
    assert cli.main(["update"]) == 1
```

(Write the two sketched bodies out fully in the test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/test_cli.py -k update -q`
Expected: FAIL — parser rejects `update`

- [ ] **Step 3: Implement**

In `cli.py`: `from introspect import update` at the top (with the other project imports). In `_build_parser`, after the `cron` block:

```python
    p_update = subparsers.add_parser(
        "update", help="check for and apply updates from the repo's origin"
    )
    p_update.add_argument(
        "--yes", "-y", action="store_true", help="apply without prompting"
    )
    p_update.set_defaults(handler=_cmd_update)
```

Handler (module level, near the other `_cmd_*`):

```python
def _cmd_update(args: argparse.Namespace) -> int:
    root = update.find_repo_root()
    if root is None:
        print("update requires a repo checkout (no .git found above the package)", file=sys.stderr)
        return 1
    try:
        chk = update.check(root)
    except update.UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if chk.state is update.UpdateState.UP_TO_DATE:
        print(f"already up to date ({chk.local_version})")
        return 0
    if chk.state is update.UpdateState.LOCAL_AHEAD:
        print(
            f"local checkout ({chk.local_version}) is ahead of origin ({chk.remote_version}) "
            "-- nothing to update; push or reset is your call"
        )
        return 0
    if chk.state is update.UpdateState.NO_CHANGELOG:
        print(
            "cannot compare versions (CHANGELOG.md missing or unreadable here or on origin) "
            "-- update manually: git pull && ./install.sh",
            file=sys.stderr,
        )
        return 1

    # BEHIND: show the changelist, then confirm.
    for entry in chk.new_entries:
        print(f"## {entry.version} — {entry.date}")
        for bullet in entry.bullets:
            print(f"- {bullet}")
    problems = update.preflight_problems(root)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    if not args.yes:
        reply = input(f"update to {chk.remote_version}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("not updating.")
            return 0
    result = update.apply(root, emit=print)
    if not result.ok:
        return 1
    if result.server_changed:
        print(
            "server code changed -- restart any running `introspect tui` or "
            "`introspect serve` processes to pick it up"
        )
    return 0
```

(`import sys` is already present in cli.py; verify, add if not.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/test_cli.py -q`
Expected: PASS (whole file)

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/cli.py server/tests/test_cli.py
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "cli: introspect update -- check, changelist, [y/N], apply (spec §5)"
```

---

### Task 6: TUI — `/update`, `/restart`, version lines, restart relaunch

**Files:**
- Modify: `server/src/introspect/tui/commands.py` (two new commands + /status version line + /help text)
- Modify: `server/src/introspect/tui/app.py` (banner version; `run_tui` returns the app result)
- Modify: `server/src/introspect/cli.py` (`_cmd_tui` re-execs on restart marker)
- Test: `server/tests/test_tui_commands.py`

**Interfaces:**
- Consumes: Task 4's `update` module; Task 1's `changelog.app_version()`; `WebServerManager.is_running/stop()/start(host)/local_url()`; `CommandContext` (`emit`, `web`, `exit`).
- Produces: `/update` (check + show changelist), `/update yes` (apply + web bounce + restart hint), `/restart` (exit with `"restart"` marker); `run_tui(...) -> object | None` (Textual's `App.run()` return value); `_cmd_tui` re-execs the process when the marker is `"restart"`.

**Consent-UX note (spec §5 adapted to the TUI's idiom):** the TUI has a single command Input and a RichLog — a modal `[y/N]` doesn't fit its existing command grammar. The y/N gate is expressed as the two-step `/update` (show) → `/update yes` (apply). The restart offer is the printed `/restart` hint; typing it IS the consent. The CLI keeps the literal `[y/N]` prompt (Task 5).

**Restart note:** a same-process relaunch loop would keep every already-imported module — old code. The restart marker therefore triggers `os.execvp(sys.argv[0], sys.argv)` in `_cmd_tui` AFTER `app.run()` has returned (terminal restored, no exec-from-inside-Textual): a fresh process, fresh imports, updated code.

- [ ] **Step 1: Write the failing tests**

Follow `test_tui_commands.py`'s existing fake-`CommandContext` pattern (the file shows how `ctx` is built and `emit` captured). Add, monkeypatching `introspect.tui.commands.upd` members:

```python
def test_update_check_shows_changelist_and_hint(...) -> None:
    # monkeypatch upd.find_repo_root -> Path("/repo"); upd.check -> BEHIND with one Entry
    _cmd_update(ctx, [])
    out = "\n".join(emitted)
    assert "1.2.0" in out and "New thing." in out
    assert "/update yes" in out          # the consent hint
    # apply monkeypatched to raise AssertionError -- check alone must never apply


def test_update_yes_applies_bounces_web_and_hints_restart(...) -> None:
    # BEHIND check; preflight_problems -> []; apply -> ApplyResult(ok=True, server_changed=True)
    # fake ctx.web: is_running True; record stop()/start() calls
    _cmd_update(ctx, ["yes"])
    out = "\n".join(emitted)
    assert web.stopped and web.started    # bounced
    assert "/restart" in out              # server changed -> restart hint


def test_update_yes_web_only_reports_updated(...) -> None:
    # apply -> ApplyResult(ok=True, server_changed=False); ctx.web.is_running False
    _cmd_update(ctx, ["yes"])
    assert any("updated to 1.2.0" in line for line in emitted)
    assert not any("/restart" in line for line in emitted)


def test_update_up_to_date(...) -> None:
    _cmd_update(ctx, [])
    assert any("already up to date (1.1.0)" in line for line in emitted)


def test_update_preflight_problems_block_apply(...) -> None:
    # preflight_problems -> ["working tree has uncommitted..."]; apply raises AssertionError if called
    _cmd_update(ctx, ["yes"])
    assert any("uncommitted" in line for line in emitted)


def test_restart_exits_with_marker(...) -> None:
    _cmd_restart(ctx, [])
    assert ctx_exit_calls == ["restart"]   # ctx.exit called with the marker


def test_status_includes_version_line(...) -> None:
    # monkeypatch commands.changelog.app_version -> "1.2.0"; run _cmd_status with the file's fixture
    assert any(line == "version: 1.2.0" for line in emitted)
```

Plus, in `server/tests/test_cli.py`, the exec-relaunch branch:

```python
def test_tui_restart_marker_execs_fresh_process(monkeypatch) -> None:
    execs: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli.os, "execvp", lambda file, argv: execs.append((file, argv)))
    monkeypatch.setattr("introspect.tui.app.run_tui", lambda **kw: "restart")
    # invoke `tui` through the file's usual main()/parser pattern with a valid --db tmp path
    assert execs == [(sys.argv[0], sys.argv)]


def test_tui_normal_exit_does_not_exec(monkeypatch) -> None:
    # same setup, run_tui returns None -> execvp never called, exit code 0
```

(Write the second body out fully; note `_cmd_tui` imports `run_tui` lazily inside the
handler, so monkeypatch the source module path as shown.)

(Adapt each `...` body to the file's local fixture style; write them out fully.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/test_tui_commands.py -q`
Expected: FAIL — `_cmd_update` / `_cmd_restart` don't exist

- [ ] **Step 3: Implement**

`commands.py` — imports:

```python
from introspect import changelog
from introspect import update as upd
```

Command handlers (near the other `_cmd_*`; `LOCAL_HOST` comes from the module's existing webserver imports — check the top of the file):

```python
def _cmd_update(ctx: CommandContext, args: list[str]) -> None:
    if args not in ([], ["yes"]):
        ctx.emit("usage: /update [yes]")
        return
    root = upd.find_repo_root()
    if root is None:
        ctx.emit("update: requires a repo checkout (no .git found)")
        return
    try:
        chk = upd.check(root)
    except upd.UpdateError as exc:
        ctx.emit(f"update: {exc}")
        return
    if chk.state is upd.UpdateState.UP_TO_DATE:
        ctx.emit(f"already up to date ({chk.local_version})")
        return
    if chk.state is upd.UpdateState.LOCAL_AHEAD:
        ctx.emit(
            f"local checkout ({chk.local_version}) is ahead of origin "
            f"({chk.remote_version}) -- nothing to update"
        )
        return
    if chk.state is upd.UpdateState.NO_CHANGELOG:
        ctx.emit("update: cannot compare versions -- update manually: git pull && ./install.sh")
        return

    for entry in chk.new_entries:
        ctx.emit(f"## {entry.version} — {entry.date}")
        for bullet in entry.bullets:
            ctx.emit(f"- {bullet}")
    problems = upd.preflight_problems(root)
    if problems:
        for problem in problems:
            ctx.emit(f"update: {problem}")
        return
    if args != ["yes"]:
        ctx.emit(f"new version {chk.remote_version} available -- type '/update yes' to apply")
        return

    result = upd.apply(root, emit=ctx.emit)
    if not result.ok:
        ctx.emit("update failed -- fix the problem above, then '/update yes' to retry")
        return
    if ctx.web.is_running:
        ctx.web.stop()
        started = ctx.web.start(LOCAL_HOST)
        if started is StartResult.STARTED:
            ctx.emit(f"web server restarted with the new UI at {ctx.web.local_url()}")
        else:
            ctx.emit(f"web server did not restart cleanly ({started.name}) -- /start-web to retry")
    if result.server_changed:
        ctx.emit("server code changed -- type /restart to relaunch the TUI on the new code")
    else:
        ctx.emit(f"updated to {chk.remote_version}")


def _cmd_restart(ctx: CommandContext, args: list[str]) -> None:
    ctx.exit("restart")
```

(If `StartResult` isn't already imported in commands.py, import it from `introspect.tui.webserver`. If `CommandContext.exit` is typed `Callable[[], None]`, widen it to `Callable[..., None]` — it binds Textual's `App.exit(result=None)`.)

In `build_registry()`, register in the existing style: `update` with `background=True` (it fetches and runs subprocesses; must not block the UI thread), help text `"/update [yes] -- check for a new version; 'yes' applies it"`, and `restart` with `background=False`, help `"/restart -- relaunch the TUI (picks up updated code)"`. Add both to `/help`'s listing the same way the neighbors do.

`/status` version line — in `_cmd_status`, first `ctx.emit`:

```python
    ctx.emit(f"version: {changelog.app_version()}")
```

`app.py` — `on_mount` banner becomes (never print the ugly `vunknown`):

```python
        version = changelog.app_version()
        label = f"v{version}" if version != "unknown" else "(version unknown)"
        log.write(
            f"introspect {label} -- type to search, /help for commands, /quit to exit"
        )
```

(import `changelog` at the top), and `run_tui` returns the run result:

```python
def run_tui(...) -> object | None:
    ...
    try:
        result = app.run()
    finally:
        app.stop_web()
    return result
```

(match the existing body; the only change is capturing/returning `app.run()`.)

`cli.py` — `_cmd_tui`'s tail becomes:

```python
    result = run_tui(db_path=dbp, source_root=config.source_root(args.source_root))
    if result == "restart":
        # A same-process relaunch would keep stale imports; exec a fresh process so the
        # updated server code actually loads. app.run() has returned: terminal restored.
        os.execvp(sys.argv[0], sys.argv)
    return 0
```

(add `import os` / `import sys` if missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/test_tui_commands.py tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Run the whole server suite, lint, commit**

Run: `cd server && uv run pytest -q && uv run ruff check .`

```bash
git add server/src/introspect/tui/commands.py server/src/introspect/tui/app.py server/src/introspect/cli.py server/tests/test_tui_commands.py
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "tui: /update + /restart, version in banner and /status, exec relaunch (spec §3,§5)"
```

---

### Task 7: Cache policy on UI serving

**Files:**
- Modify: `server/src/introspect/api/__init__.py` (SPA middleware + assets headers)
- Test: the existing UI-serving test file (find with `grep -l "ui_dist\|SPA\|assets" server/tests/test_api_*.py`)

**Interfaces:**
- Consumes: the existing `_serve_ui_fallback` middleware and `/assets` StaticFiles mount.
- Produces: `Cache-Control: no-cache` on `index.html`/SPA-fallback/dist-root files; `Cache-Control: public, max-age=31536000, immutable` on `/assets/*`.

- [ ] **Step 1: Write the failing tests**

In the UI-serving test file (it already builds an app over a fixture dist — reuse that fixture):

```python
def test_index_and_spa_fallback_are_no_cache(...) -> None:
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/some/spa/route").headers["cache-control"] == "no-cache"


def test_hashed_assets_are_immutable(...) -> None:
    # the fixture dist contains at least one file under assets/ -- request it
    resp = client.get("/assets/<fixture asset filename>")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && uv run pytest tests/<ui serving test file> -q`
Expected: FAIL — no cache-control headers today

- [ ] **Step 3: Implement**

In `_serve_ui_fallback` (api/__init__.py): the middleware already sees every response, including the `/assets` mount's. Set headers there:

```python
            @app.middleware("http")
            async def _serve_ui_fallback(request: Request, call_next) -> Response:
                response = await call_next(request)
                # NOTE(claude): vite content-hashes /assets filenames, so they are safe to
                # cache forever; index.html is NOT hashed and must revalidate every load
                # (no-cache != no-store: ETag/Last-Modified still make the common case a 304).
                # Without this split a stale browser cache can hide a fresh build -- the
                # 2026-08-08 work-machine bug's second layer.
                if request.url.path.startswith("/assets/") and response.status_code == 200:
                    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                    return response
                if response.status_code != 404 or request.method not in ("GET", "HEAD"):
                    return response
                path = request.url.path.lstrip("/")
                if path.startswith("api/"):
                    return response
                if path:
                    candidate = (resolved_ui_dist / path).resolve()
                    try:
                        candidate.relative_to(resolved_ui_dist.resolve())
                    except ValueError:
                        candidate = None
                    if candidate is not None and candidate.is_file():
                        return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
                return FileResponse(
                    resolved_ui_dist / "index.html", headers={"Cache-Control": "no-cache"}
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && uv run pytest tests/<ui serving test file> -q` then the full `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/api/__init__.py server/tests/<ui serving test file>
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "api: cache policy -- no-cache shell, immutable hashed assets (spec §6)"
```

---

### Task 8: Web — baked version + StatusBar display

**Files:**
- Create: `web/src/version.ts`
- Create: `web/src/globals.d.ts`
- Modify: `web/vite.config.ts`
- Modify: `web/src/api/types.ts` (StatusOut)
- Modify: `web/src/components/StatusBar.tsx`
- Test: `web/tests/StatusBar.test.tsx` (extend) — plus a fixture sweep for the new `StatusOut.version` field

**Interfaces:**
- Consumes: `StatusOut.version` from Task 3; `__APP_VERSION__` compile-time define.
- Produces: `UI_VERSION: string` (from `web/src/version.ts`); StatusBar version chip.

- [ ] **Step 1: Write the failing tests**

Extend `web/tests/StatusBar.test.tsx` in its existing style (vi.hoisted + vi.mock of `../src/api/client`). Mock the version seam, not the global:

```tsx
vi.mock('../src/version', () => ({ UI_VERSION: '1.2.0' }))
```

```tsx
it('shows a single version chip when ui and server agree', async () => {
  fetchStatus.mockResolvedValue(makeStatus({ version: '1.2.0' }))
  render(<StatusBar />, { wrapper })
  expect(await screen.findByText('v1.2.0')).toBeInTheDocument()
})

it('shows both versions when they differ', async () => {
  fetchStatus.mockResolvedValue(makeStatus({ version: '1.3.0' }))
  render(<StatusBar />, { wrapper })
  expect(await screen.findByText('ui v1.2.0 · server v1.3.0')).toBeInTheDocument()
})

it('omits the chip when the server version is unknown', async () => {
  fetchStatus.mockResolvedValue(makeStatus({ version: 'unknown' }))
  render(<StatusBar />, { wrapper })
  await screen.findByText(/last import/)   // bar rendered
  expect(screen.queryByText(/^v|ui v/)).not.toBeInTheDocument()
})
```

(`makeStatus` = the file's existing status-fixture helper, extended with a `version` field; if none exists, add one local to the test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- --run StatusBar`
Expected: FAIL — `../src/version` doesn't exist / no chip rendered

- [ ] **Step 3: Implement**

`web/src/globals.d.ts`:

```ts
// Injected by vite's `define` (vite.config.ts) from ../CHANGELOG.md at build time.
declare const __APP_VERSION__: string
```

`web/src/version.ts`:

```ts
// NOTE(claude): the version is baked at BUILD time, deliberately: a stale dist then
// reports its own stale version instead of borrowing currency from the API (spec §3).
// The typeof guard keeps non-vite tools (and tests that mock this module) safe.
export const UI_VERSION: string =
  typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown'
```

`web/vite.config.ts` — add above `defineConfig`:

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

function changelogVersion(): string {
  try {
    const text = readFileSync(fileURLToPath(new URL('../CHANGELOG.md', import.meta.url)), 'utf-8')
    const match = text.match(/^## (\d+\.\d+\.\d+) /m)
    return match ? match[1] : 'unknown'
  } catch {
    return 'unknown'
  }
}
```

and inside the config object:

```ts
  define: {
    __APP_VERSION__: JSON.stringify(changelogVersion()),
  },
```

`web/src/api/types.ts` — `StatusOut` gains `version: string` (first field, matching the server model).

`web/src/components/StatusBar.tsx` — import `UI_VERSION` from `../version`; add:

```tsx
function versionText(serverVersion: string): string | null {
  if (serverVersion === 'unknown' || UI_VERSION === 'unknown') return null
  if (serverVersion === UI_VERSION) return `v${UI_VERSION}`
  return `ui v${UI_VERSION} · server v${serverVersion}`
}
```

and in the right-hand span (before the anomalies span, inside the existing `{status && (...)}` block):

```tsx
            {versionText(status.version) && <span>{versionText(status.version)}</span>}
```

- [ ] **Step 4: Fixture sweep + full verification**

`StatusOut` grew a required field: `grep -rn "archive_bytes" web/tests web/src` and add `version: '1.2.0'` (or scenario-appropriate) to every StatusOut fixture. Then run ALL of:

```bash
cd web && npm test -- --run && npm run lint && npm run build
```

Expected: all green — `npm run build` includes `tsc -b`, which is what catches fixture drift (this exact gap shipped typecheck debt once before; don't skip the build).

- [ ] **Step 5: Commit**

```bash
git add web/src/version.ts web/src/globals.d.ts web/vite.config.ts web/src/api/types.ts web/src/components/StatusBar.tsx web/tests/StatusBar.test.tsx <any fixture files touched>
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "web: build-time version + StatusBar chip with ui/server mismatch display (spec §3)"
```

---

### Task 9: CHANGELOG backfill, docs, full-suite verification

**Files:**
- Create: `CHANGELOG.md`
- Create: `docs/user/update.md`
- Modify: `README.md` (update story), `docs/user/README.md` (index), `docs/user/tui.md` (commands), `docs/user/install.md` (update pointer), `docs/dev/README.md` (release ritual)

**Interfaces:** none produced — this task makes the feature true in the repo's own files.

- [ ] **Step 1: Write `CHANGELOG.md`**

```markdown
# Changelog

The top entry is the current version. Entries are written for users: what changed
in what you can see and do. Format: `## MAJOR.MINOR.PATCH — YYYY-MM-DD` followed
by `- ` bullets.

## 1.2.0 — 2026-08-08
- The reading room and TUI now show which version they're running; the status bar
  flags when the UI and server versions differ (a stale build is now visible).
- `/update` in the TUI (and `introspect update` in the CLI) checks for new
  versions, shows what's new, and applies the update — including rebuilding the
  web UI, the step `git pull` alone never did.
- New `update.sh`: one command to pull the latest version and re-converge.
- Fixed browser caching that could keep showing an old reading room after an
  update (hard-refresh no longer needed).

## 1.1.0 — 2026-08-08
- Every message now says who was actually talking: YOU only for words you typed;
  harness-delivered content (tool results, skill injections, dispatch prompts,
  task notifications) is labeled for what it is.
- Three reading modes — chat, chat+harness, all — replace the "conversation only"
  toggle.
- The raw-record inspector shows each record's authorship classification.
- The archive tolerates hand-pretty-printed transcript files and heals records
  previously mis-split by them.

## 1.0.0 — 2026-08-01
- V1: one-command installer, byte-faithful archive with 15-minute cron belt,
  full-text search, and the Still Water reading room (sidebar search, project
  filter, editable titles, resume links).
```

(Verify the 1.1.0 bullets against `git log --oneline` since the V1 tag-point and adjust honestly — the bullets above are the expected shape, not gospel. 1.0.0's date: use the actual V1 landing date from `git log`, not 2026-08-01, if it differs.)

- [ ] **Step 2: Write `docs/user/update.md`**

Cover, in the user guide's existing voice: what `/update` does (check → changelist → `/update yes`); the CLI twin (`introspect update [--yes]`); `update.sh` for script users; that updates are fast-forward-only and never touch uncommitted work; the restart offer and why it appears (`server code changed`); the version chip in the status bar and what a `ui vX · server vY` mismatch means (stale build — run `/update` or `./update.sh`); troubleshooting (dirty tree message, no-upstream message, `LOCAL_AHEAD` for people hacking on the repo).

- [ ] **Step 3: Update the other docs**

- `README.md`: in "Get set up", after the install steps, add an "Updating" subsection: `/update` in the TUI, `./update.sh` from a shell, re-running `./install.sh` still works. Trim the existing "re-running it is how you update" phrasing to point at the new section.
- `docs/user/README.md`: add `update.md` to the task index.
- `docs/user/tui.md`: document `/update [yes]` and `/restart` in the command walkthrough.
- `docs/user/install.md`: short "updating later" pointer to `update.md`.
- `docs/dev/README.md`: a "Release ritual" paragraph — any user-visible change lands with a `CHANGELOG.md` entry in the same commit series; top entry is the version every surface reports; the grammar line; plans must include the changelog edit.

- [ ] **Step 4: Full verification**

```bash
cd server && uv run pytest -q && uv run ruff check .
cd ../web && npm test -- --run && npm run lint && npm run build
```

Expected: all green. Then confirm the built bundle actually baked the version: `grep -o '1\.2\.0' web/dist/assets/*.js | head -1` (nonempty).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/user/update.md README.md docs/user/README.md docs/user/tui.md docs/user/install.md docs/dev/README.md
git commit --author "Claude (<your tier>) <noreply@anthropic.com>" -m "docs+changelog: versions, /update story, release ritual (spec §2,§8)"
```

---

## Post-plan verification (orchestrator, not a task)

Manual walk on this machine (spec §8): `uv run introspect tui` → banner shows `v1.2.0` → `/status` shows the version line → `/update` reports `already up to date (1.2.0)` → `/start-web`, open the room, StatusBar shows `v1.2.0`, response headers show the cache policy. Then the real field test on the work machine: `git pull` (stale as before) → room shows `ui v1.1.0-ish · server v…` mismatch after server restart, or straight to `/update` → changelist → `/update yes` → new room without hard-refresh.
