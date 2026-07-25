# Resume Links Implementation Plan (spec §17)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One click next to a session's title checks the live `.jsonl`, restores it from the archive if missing, and opens a terminal in the session's original directory with `claude --resume <uuid>` running — with an honest degradation ladder when any step can't happen.

**Architecture:** A new `resume.py` server module (cron.py architecture: pure script-builder + orchestration behind an injectable `Runner`), one POST endpoint, `on_disk` on `SessionDetail`, and a `ResumeButton` in the reader header. The launch artifact is a self-falling-back `.command` script run via `open -a <terminal>` (LaunchServices — no AppleScript, no TCC prompt); the script itself handles claude-not-on-PATH (pbcopy fallback) because it runs in the user's login shell, the only environment where launchability is true.

**Tech Stack:** FastAPI/SQLAlchemy/pytest (server), React/TypeScript/react-query/Vitest+RTL (web). No new dependencies.

## Global Constraints

- **Zero-legacy law (pre-release):** no deprecation aliases, no legacy params — delete, don't deprecate.
- **The name "Donovan" must NOT appear in any committable text** — attribute rulings to `relativityboy`.
- **This feature is the FIRST writer into `source_root`.** The write is presence-gated: an existing live file is NEVER touched, diffed, or overwritten (spec §17.1.2 — the live file may be ahead of the archive). No other code path may gain writes as a side effect.
- Archived sessions are indistinguishable from nonexistent on every API path (§15.1) — resume 404s them via the direct `ArchivedSession` probe, never a 409.
- Commits: `git commit --author="Claude (Fable 5) <noreply@anthropic.com>"`, terse area-prefixed messages (`server:`, `web:`, `docs:`).
- Suites must stay green at every commit: `cd server && uv run pytest` · `uv run ruff check .` · `cd web && npx vitest run` · `npm run lint` · `npm run build`.
- Still Water styling: header meta-row controls are mono 11px; actions use `var(--dragonfly)`, quiet text uses `var(--mist)`. No new toast system.

---

### Task 1: `config.terminal_app()`

**Files:**
- Modify: `server/src/introspect/config.py`
- Test: `server/tests/test_config.py` (extend if it exists; create otherwise)

**Interfaces:**
- Produces: `config.terminal_app(cli_value: str | None = None) -> str` and `config.DEFAULT_TERMINAL_APP = "Terminal"`. Task 4's `create_app` calls `config.terminal_app(...)`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_config.py (add; create the file with these if absent)
from introspect import config


def test_terminal_app_default(monkeypatch) -> None:
    monkeypatch.delenv("INTROSPECT_TERMINAL_APP", raising=False)
    assert config.terminal_app() == "Terminal"


def test_terminal_app_env_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("INTROSPECT_TERMINAL_APP", "iTerm")
    assert config.terminal_app() == "iTerm"


def test_terminal_app_explicit_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("INTROSPECT_TERMINAL_APP", "iTerm")
    assert config.terminal_app("Ghostty") == "Ghostty"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'introspect.config' has no attribute 'terminal_app'`

- [ ] **Step 3: Implement**

```python
# server/src/introspect/config.py — append after source_root()
DEFAULT_TERMINAL_APP = "Terminal"


def terminal_app(cli_value: str | None = None) -> str:
    """Resolve the macOS terminal application name used by resume links (spec §17.1.5)."""
    return cli_value or os.environ.get("INTROSPECT_TERMINAL_APP") or DEFAULT_TERMINAL_APP
```
(Also move/keep `DEFAULT_TERMINAL_APP` next to the other `DEFAULT_*` constants at the top — match the file's existing layout.)

- [ ] **Step 4: Run to verify pass**

Run: `cd server && uv run pytest tests/test_config.py -v` → PASS; then `uv run ruff check .`

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/config.py server/tests/test_config.py
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "server: config.terminal_app — INTROSPECT_TERMINAL_APP, default Terminal (spec §17.1.5)"
```

---

### Task 2: `resume.py` — outcome types + script builder (pure)

**Files:**
- Create: `server/src/introspect/resume.py`
- Test: `server/tests/test_resume.py`

**Interfaces:**
- Consumes: `Runner` type alias from `introspect.cron` (`(argv, stdin) -> (code, out, err)`).
- Produces: `ResumeOutcome` dataclass `{restored: bool, launched: bool, mode: str, command: str, cwd: str | None, live_path: str, detail: str | None}`; mode constants `MODE_LAUNCHED = "launched"`, `MODE_MISSING_CWD = "missing_cwd"`, `MODE_OPEN_FAILED = "open_failed"`, `MODE_UNSUPPORTED = "unsupported_platform"`; `build_resume_command(session_uuid: str) -> str`; `build_launch_script(cwd: str, session_uuid: str) -> str`. Task 3 adds `resume_session` to this same module.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest tests/test_resume.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` on `introspect.resume`

- [ ] **Step 3: Implement**

```python
# server/src/introspect/resume.py
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
```
(`Session`, `Path`, `sys`, `SessionNotFoundError`, `export_session_to`, `ChatSession`, `Project` are consumed by Task 3's `resume_session` in this same module — ruff will flag them as unused until then; EITHER add them in Task 3's diff instead, or land Task 3 in the same session. Preferred: add only `shlex`/`subprocess`/`dataclass`/`Runner` imports now, the rest in Task 3.)

- [ ] **Step 4: Run to verify pass**

Run: `cd server && uv run pytest tests/test_resume.py -v` → PASS; `uv run ruff check .` → clean (trim imports per the note above).

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/resume.py server/tests/test_resume.py
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "server: resume script builder — zsh -l self-falling-back .command, hostile-cwd quoting (spec §17.1.4)"
```

---

### Task 3: `resume_session()` orchestration

**Files:**
- Modify: `server/src/introspect/resume.py`
- Test: `server/tests/test_resume.py` (extend)

**Interfaces:**
- Consumes: `export_session_to(db, session_uuid, out_path) -> int` and `SessionNotFoundError` from `introspect.export`; `ChatSession`/`Project` models; conftest fixtures `db_session`, `fixture_tree`, constants `PROJECT_SLUG_1`, `SESSION_UUID_1`; `discover`/`capture_file` for ingest (same pattern as `test_api_archive.py`).
- Produces: `resume_session(db, session_uuid, *, source_root: Path, terminal_app: str, scripts_dir: Path, runner: Runner | None = None) -> ResumeOutcome` — Task 4's route calls exactly this. Platform check reads `sys.platform` directly; tests monkeypatch it.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_resume.py — additions
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.export import SessionNotFoundError, export_transcript
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import Project

from .conftest import PROJECT_SLUG_1, SESSION_UUID_1


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
```
(If `from .conftest import ...` doesn't match the suite's existing import style, copy how `test_api_archive.py` imports `SESSION_UUID_1` and use that exact form.)

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest tests/test_resume.py -v`
Expected: new tests FAIL — `AttributeError: module 'introspect.resume' has no attribute 'resume_session'`

- [ ] **Step 3: Implement**

```python
# server/src/introspect/resume.py — append (plus the imports deferred from Task 2)
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
```
Path-safety note (keep as a code comment if you find it non-obvious): `session_uuid` reaches the filename only after `db.get(ChatSession, ...)` succeeds — only uuids that exist as primary keys proceed, so no traversal via crafted ids.

- [ ] **Step 4: Run to verify pass**

Run: `cd server && uv run pytest tests/test_resume.py -v` → PASS; `uv run pytest` (full suite) → PASS; `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/resume.py server/tests/test_resume.py
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "server: resume_session — presence-gated restore via export_session_to, injectable open runner, honest mode ladder (spec §17.1/§17.3)"
```

---

### Task 4: `ResumeResult` model + POST endpoint + app wiring

**Files:**
- Modify: `server/src/introspect/api/models.py` (add `ResumeResult`)
- Create: `server/src/introspect/api/routes/resume.py`
- Modify: `server/src/introspect/api/__init__.py` (state + router)
- Test: `server/tests/test_api_resume.py`

**Interfaces:**
- Consumes: `resume_session(...)` (Task 3), `config.terminal_app` (Task 1), `_require_session`-style guards (pattern from `routes/archive.py`), `get_db` dep, `app.state.{source_root, db_path}`.
- Produces: `POST /api/v1/sessions/{session_uuid}/resume` → 200 `ResumeResult {restored, launched, mode, command, cwd, live_path, detail}`; `create_app(..., terminal_app: str | None = None, resume_runner: Runner | None = None)` setting `app.state.terminal_app` and `app.state.resume_runner`. Task 6's `postResume` calls this endpoint.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_api_resume.py
"""POST /api/v1/sessions/{uuid}/resume (spec §17.2).

Same wiring as test_api_archive.py: app over the pinned fixture tree sharing db_session's DB
file — but with fixture_tree as the app's SOURCE ROOT (resume reads AND writes it) and a fake
resume_runner so no test ever spawns `open`.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.export import export_transcript
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import Project

from .conftest import PROJECT_SLUG_1, SESSION_UUID_1


class FakeOpen:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str, str]:
        self.calls.append(argv)
        return (0, "", "")


@pytest.fixture
def fake_open() -> FakeOpen:
    return FakeOpen()


@pytest.fixture
def client(
    db_session: Session, fixture_tree: Path, tmp_path: Path, fake_open: FakeOpen, monkeypatch
) -> TestClient:
    monkeypatch.setattr(sys, "platform", "darwin")
    for f in discover(fixture_tree):
        capture_file(db_session, f)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    project = db_session.scalars(select(Project).where(Project.dir_slug == PROJECT_SLUG_1)).one()
    project.resolved_cwd = str(workdir)
    db_session.commit()
    app = create_app(
        db_path=tmp_path / "archive.db",
        source_root=fixture_tree,
        terminal_app="TestTerm",
        resume_runner=fake_open,
    )
    return TestClient(app)


def test_resume_unknown_session_404(client: TestClient) -> None:
    resp = client.post("/api/v1/sessions/00000000-0000-0000-0000-000000000000/resume")
    assert resp.status_code == 404


def test_resume_archived_session_404(client: TestClient) -> None:
    assert client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive").status_code == 204
    resp = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert resp.status_code == 404  # indistinguishable from nonexistent (§15.1)


def test_resume_present_file_launches(client: TestClient, fake_open: FakeOpen) -> None:
    resp = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["restored"] is False
    assert body["launched"] is True
    assert body["mode"] == "launched"
    assert body["command"] == f"claude --resume {SESSION_UUID_1}"
    assert len(fake_open.calls) == 1
    assert fake_open.calls[0][:3] == ["open", "-a", "TestTerm"]


def test_resume_restores_missing_file_byte_identical(
    client: TestClient, db_session: Session, fixture_tree: Path
) -> None:
    live = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl"
    expected = export_transcript(db_session, SESSION_UUID_1)
    live.unlink()
    body = client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume").json()
    assert body["restored"] is True
    assert live.read_bytes() == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest tests/test_api_resume.py -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'terminal_app'` (first), then 404s on the route once wiring exists.

- [ ] **Step 3: Implement**

`server/src/introspect/api/models.py` — add:

```python
class ResumeResult(BaseModel):
    """POST /sessions/{uuid}/resume outcome (spec §17.2/§17.3). `mode` is the launch outcome
    only; `restored` is orthogonal."""

    restored: bool
    launched: bool
    mode: str
    command: str
    cwd: str | None
    live_path: str
    detail: str | None
```

`server/src/introspect/api/routes/resume.py` — create:

```python
"""Resume endpoint (spec §17.2): ``POST /api/v1/sessions/{uuid}/resume``.

POST, not PUT — each call may spawn a terminal window; it is not idempotent state. HTTP errors
only when we can't even try: 404 unknown AND 404 archived (§15.1 — archived sessions are
indistinguishable from nonexistent on every API path; direct probe, admin-export precedent).
Everything downstream — missing cwd, `open` failure, non-darwin — is a 200 with an honest
``mode`` (§17.3): a failed launch after a successful restore is an outcome to report, not an
exception. The subprocess edge is injectable via ``app.state.resume_runner`` (None in
production → real subprocess), the CrontabIO seam adapted to FastAPI state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import ResumeResult
from introspect.models import ArchivedSession, ChatSession
from introspect.resume import resume_session

router = APIRouter(prefix="/api/v1")


@router.post("/sessions/{session_uuid}/resume", response_model=ResumeResult)
def resume_session_endpoint(
    session_uuid: str, request: Request, db: Session = Depends(get_db)
) -> ResumeResult:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")
    if db.get(ArchivedSession, session_uuid) is not None:
        raise LookupError(f"session {session_uuid} not found")
    outcome = resume_session(
        db,
        session_uuid,
        source_root=request.app.state.source_root,
        terminal_app=request.app.state.terminal_app,
        scripts_dir=request.app.state.db_path.parent / "resume-scripts",
        runner=request.app.state.resume_runner,
    )
    return ResumeResult(
        restored=outcome.restored,
        launched=outcome.launched,
        mode=outcome.mode,
        command=outcome.command,
        cwd=outcome.cwd,
        live_path=outcome.live_path,
        detail=outcome.detail,
    )
```

`server/src/introspect/api/__init__.py` — three edits, matching existing style:
1. `create_app` signature gains `terminal_app: str | None = None, resume_runner: "Runner | None" = None` (import `Runner` from `introspect.cron`).
2. After `app.state.source_root = ...`:
```python
    app.state.terminal_app = config.terminal_app(terminal_app)
    app.state.resume_runner = resume_runner  # tests inject a fake; None = real subprocess
```
3. Import the router and register it with the others: `app.include_router(resume_router)`.

- [ ] **Step 4: Run to verify pass**

Run: `cd server && uv run pytest tests/test_api_resume.py -v` → PASS; `uv run pytest` full → PASS; `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/api/models.py server/src/introspect/api/routes/resume.py server/src/introspect/api/__init__.py server/tests/test_api_resume.py
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "server: POST /sessions/{uuid}/resume — 404 unknown+archived, injectable runner on app.state, ResumeResult (spec §17.2)"
```

---

### Task 5: `on_disk` on `SessionDetail`

**Files:**
- Modify: `server/src/introspect/api/models.py` (`SessionDetail`)
- Modify: `server/src/introspect/api/routes/sessions.py` (`get_session`)
- Test: `server/tests/test_api_resume.py` (extend — the flip test needs the resume endpoint)

**Interfaces:**
- Consumes: `request.app.state.source_root`; the detail route's existing row (has `project_slug`).
- Produces: `SessionDetail.on_disk: bool` — Task 6's button label reads it.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_api_resume.py — append
def test_on_disk_flips_after_restore(client: TestClient, fixture_tree: Path) -> None:
    live = fixture_tree / PROJECT_SLUG_1 / f"{SESSION_UUID_1}.jsonl"
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is True
    live.unlink()
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is False
    client.post(f"/api/v1/sessions/{SESSION_UUID_1}/resume")
    assert client.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()["on_disk"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd server && uv run pytest tests/test_api_resume.py::test_on_disk_flips_after_restore -v`
Expected: FAIL — `KeyError: 'on_disk'`

- [ ] **Step 3: Implement**

`api/models.py`: add `on_disk: bool = False` to `SessionDetail` (detail-only — the sidebar has no resume affordance, §17.7; default keeps `from_attributes` construction paths valid).

`routes/sessions.py` `get_session`: ensure the handler has a `request: Request` parameter (add it if absent, matching FastAPI style used elsewhere in the file). Where the `SessionDetail` response is constructed, compute and pass:

```python
    live_path = (
        request.app.state.source_root / row.project_slug / f"{session_uuid}.jsonl"
    )
    # ... into the existing SessionDetail construction:
    on_disk=live_path.exists(),
```
(One `stat` per detail read — §17.4. `row.project_slug` is whatever name the detail query already exposes for the project's `dir_slug`; reuse it, don't add a second join.)

- [ ] **Step 4: Run to verify pass**

Run: `cd server && uv run pytest` full → PASS; `uv run ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/api/models.py server/src/introspect/api/routes/sessions.py server/tests/test_api_resume.py
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "server: SessionDetail.on_disk — one stat at detail read, flips after restore (spec §17.4)"
```

---

### Task 6: Web — types, client, hook, `ResumeButton`, header integration

**Files:**
- Modify: `web/src/api/types.ts` (`ResumeResult`, `SessionDetail.on_disk`)
- Modify: `web/src/api/client.ts` (`postResume`)
- Modify: `web/src/api/hooks.ts` (`useResumeSession`)
- Create: `web/src/components/ResumeButton.tsx`
- Modify: `web/src/routes/SessionPage.tsx` (header meta row)
- Test: `web/tests/ResumeButton.test.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/sessions/{uuid}/resume` (Task 4), `SessionDetail.on_disk` (Task 5), `apiFetch`, react-query patterns from `useArchiveSession`.
- Produces: `<ResumeButton sessionUuid={string} onDisk={boolean} />`.

- [ ] **Step 1: Write the failing tests**

```tsx
// web/tests/ResumeButton.test.tsx — ArchiveButton.test.tsx conventions: mock the client module,
// real useMutation runs.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResumeResult } from '../src/api/types'
import { ResumeButton } from '../src/components/ResumeButton'

const { postResume } = vi.hoisted(() => ({ postResume: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, postResume }
})

const LAUNCHED: ResumeResult = {
  restored: false,
  launched: true,
  mode: 'launched',
  command: 'claude --resume uuid-1',
  cwd: '/Users/casey/projects/myapp',
  live_path: '/tmp/x.jsonl',
  detail: null,
}

function renderButton(onDisk = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <ResumeButton sessionUuid="uuid-1" onDisk={onDisk} />
    </QueryClientProvider>,
  )
  return { invalidateSpy, ...utils }
}

beforeEach(() => {
  postResume.mockReset()
  postResume.mockResolvedValue(LAUNCHED)
})

describe('ResumeButton', () => {
  it('labels honestly from on_disk', () => {
    renderButton(true)
    expect(screen.getByRole('button', { name: '⟲ resume' })).toBeInTheDocument()
  })

  it('labels restore & resume when the live file is gone', () => {
    renderButton(false)
    expect(screen.getByRole('button', { name: '⟲ restore & resume' })).toBeInTheDocument()
  })

  it('posts and reports a launch, invalidating the session cache', async () => {
    const { invalidateSpy } = renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    expect(postResume).toHaveBeenCalledWith('uuid-1')
    await waitFor(() => expect(screen.getByText('launched')).toBeInTheDocument())
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] }),
    )
  })

  it('composes restored + launched', async () => {
    postResume.mockResolvedValue({ ...LAUNCHED, restored: true })
    renderButton(false)
    await userEvent.click(screen.getByRole('button', { name: '⟲ restore & resume' }))
    await waitFor(() =>
      expect(screen.getByText('restored from archive · launched')).toBeInTheDocument(),
    )
  })

  it('keeps the command readable when the launch degrades', async () => {
    postResume.mockResolvedValue({
      ...LAUNCHED,
      launched: false,
      mode: 'missing_cwd',
      detail: '/gone/dir',
    })
    renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    await waitFor(() =>
      expect(
        screen.getByText('original directory missing (/gone/dir) — run: claude --resume uuid-1'),
      ).toBeInTheDocument(),
    )
  })

  it('reports failure without pretending', async () => {
    postResume.mockRejectedValue(new Error('boom'))
    renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    await waitFor(() => expect(screen.getByText('resume failed')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run tests/ResumeButton.test.tsx`
Expected: FAIL — cannot resolve `../src/components/ResumeButton`

- [ ] **Step 3: Implement**

`web/src/api/types.ts` — add, and extend `SessionDetail`:

```ts
export type ResumeMode = 'launched' | 'missing_cwd' | 'open_failed' | 'unsupported_platform'

export interface ResumeResult {
  restored: boolean
  launched: boolean
  mode: ResumeMode
  command: string
  cwd: string | null
  live_path: string
  detail: string | null
}
```
```ts
export interface SessionDetail extends SessionSummary {
  transcripts: TranscriptInfo[]
  on_disk: boolean
}
```

`web/src/api/client.ts` — add:

```ts
export function postResume(uuid: string): Promise<ResumeResult> {
  return apiFetch<ResumeResult>(`/sessions/${encodeURIComponent(uuid)}/resume`, {
    method: 'POST',
  })
}
```

`web/src/api/hooks.ts` — add:

```ts
export function useResumeSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (uuid: string) => postResume(uuid),
    // ['sessions'] prefixes the detail key ['sessions', uuid] — a restore flips on_disk.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sessions'] }),
  })
}
```

`web/src/components/ResumeButton.tsx` — create:

```tsx
import type { CSSProperties } from 'react'
import { useResumeSession } from '../api/hooks'
import type { ResumeResult } from '../api/types'

// Header meta-row sibling of ArchiveButton/the .jsonl link — same mono 11px voice. The button is
// dragonfly (it's an action, like the export link); the status text is mist. Fallback statuses
// keep the exact resume command readable/selectable — the room never swallows what it knows
// (spec §17.3/§17.4).
const BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: 'var(--dragonfly)',
}

const STATUS_STYLE: CSSProperties = {
  color: 'var(--mist)',
  userSelect: 'text',
}

function statusText(r: ResumeResult): string {
  const prefix = r.restored ? 'restored from archive · ' : ''
  switch (r.mode) {
    case 'launched':
      return `${prefix}launched`
    case 'missing_cwd':
      return `${prefix}original directory missing (${r.detail}) — run: ${r.command}`
    case 'open_failed':
      return `${prefix}couldn't open terminal (${r.detail}) — run: ${r.command}`
    case 'unsupported_platform':
      return `${prefix}launch is macOS-only — run: ${r.command}`
  }
}

export interface ResumeButtonProps {
  sessionUuid: string
  /** SessionDetail.on_disk — honest label before the click (§17.4). */
  onDisk: boolean
}

/** Spec §17: the door back into a conversation. POST → terminal opens with `claude --resume`
 * running; every degradation is reported in place, command included. */
export function ResumeButton({ sessionUuid, onDisk }: ResumeButtonProps) {
  const mutation = useResumeSession()
  const status = mutation.isError
    ? 'resume failed'
    : mutation.data
      ? statusText(mutation.data)
      : null

  return (
    <>
      <button
        type="button"
        className="resume-button mono"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate(sessionUuid)}
        style={BUTTON_STYLE}
      >
        {onDisk ? '⟲ resume' : '⟲ restore & resume'}
      </button>
      {status && (
        <span className="resume-status" style={STATUS_STYLE}>
          {status}
        </span>
      )}
    </>
  )
}
```

`web/src/routes/SessionPage.tsx` — in the `.session-meta` row, directly after the message-count `<span>` and before the `↓ .jsonl` link:

```tsx
            {/* §17: the door back in. Archived sessions never render this page (§15.1 detail
              404), so no archived-branch is needed here — the hiding is structural. */}
            <ResumeButton sessionUuid={session.session_uuid} onDisk={session.on_disk} />
```
(plus the import at the top of the file.)

- [ ] **Step 4: Run to verify pass**

Run: `cd web && npx vitest run` → PASS; `npm run lint` → clean; `npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/api/hooks.ts web/src/components/ResumeButton.tsx web/src/routes/SessionPage.tsx web/tests/ResumeButton.test.tsx
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "web: ResumeButton — honest resume/restore&resume label from on_disk, mode-composed status, command always readable (spec §17.4)"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/user/reading-room.md` (new "Resuming a conversation" section)
- Modify: `docs/user/install.md` (env var table/section: `INTROSPECT_TERMINAL_APP`)
- Modify: `README.md` (one line in the reading-room mini-manual)

**Interfaces:** none — prose only. Match each file's existing structure and voice (see `docs/user/cron.md` for the tone of platform-behavior notes).

- [ ] **Step 1: Write the docs**

`docs/user/reading-room.md` — add a section (place it near the export/archive affordance docs, following the file's existing order):

```markdown
## Resuming a conversation

Every conversation header has a `⟲ resume` link. Clicking it opens a terminal in the
session's original project directory with `claude --resume <session-id>` already running —
whether or not Claude Code still has the transcript.

- If the live `.jsonl` is still under `~/.claude/projects/`, it is left exactly as-is.
- If Claude Code has deleted it, the label reads `⟲ restore & resume` and the archive first
  writes the byte-identical transcript back where Claude Code expects it. Your live file is
  never overwritten — restore only happens when the file is missing.
- The terminal app defaults to macOS Terminal; set `INTROSPECT_TERMINAL_APP` (e.g. `iTerm`)
  before `introspect serve` to use another.
- If `claude` isn't on your PATH, the opened terminal copies the resume command to your
  clipboard and says so — paste and run.
- If the original project directory no longer exists, or you're not on macOS, nothing is
  launched; the reader shows the exact command to run instead.

Launching happens on the machine running `introspect serve`. That's the point on your own
Mac — but it's one more reason never to bind the server beyond 127.0.0.1.
```

`docs/user/install.md` — add `INTROSPECT_TERMINAL_APP` wherever `INTROSPECT_DB` / `INTROSPECT_SOURCE_ROOT` are documented, same format: *"Terminal application opened by resume links (macOS). Default: `Terminal`."*

`README.md` — one line in the mini-manual list, matching its style: *"`⟲ resume` — reopen any archived conversation in a terminal via `claude --resume`, restoring the transcript first if Claude Code deleted it."*

- [ ] **Step 2: Verify**

Read each edited section in place; confirm heading levels and list style match neighbors. Run `git diff --stat` — only the three doc files.

- [ ] **Step 3: Commit**

```bash
git add docs/user/reading-room.md docs/user/install.md README.md
git commit --author="Claude (Fable 5) <noreply@anthropic.com>" -m "docs: resume links — reading-room section, INTROSPECT_TERMINAL_APP, README line (spec §17.6)"
```

---

## Final verification (after all tasks)

1. Full suites: `cd server && uv run pytest && uv run ruff check .` · `cd web && npx vitest run && npm run lint && npm run build`.
2. **The walk (mandatory — repo law since Phase 3):** `cd server && uv run introspect serve`, then in the room:
   - Open a session whose file still exists → header shows `⟲ resume` → click → Terminal opens in the project directory with `claude --resume` running. Exit it.
   - Open a `gone_at_source` session (ghost) → label reads `⟲ restore & resume` → click → file appears under `~/.claude/projects/<slug>/`, terminal opens, `claude --resume` finds it. Verify the restored file: `ls -la` it, and spot-check byte count against the `↓ .jsonl` download.
   - `INTROSPECT_TERMINAL_APP=NoSuchApp uv run introspect serve` → click resume → status reports `couldn't open terminal (...) — run: claude --resume ...` with the command selectable.
   - Confirm the restored session now shows in Claude Code's own `claude --resume` picker for that project.
3. Anomaly floor check after the walk's imports: `introspect status` — floor stays 0.
