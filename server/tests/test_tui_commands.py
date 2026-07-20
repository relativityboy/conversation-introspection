"""Command registry + handler unit tests (§16), driven with a fake CommandContext.

Handlers are exercised directly (no running Textual app) against a real fixture DB, proving they
call the same underlying code the CLI verbs run. Web-touching handlers use a FakeWeb so no socket
is ever bound."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.run import run_import
from introspect.tui.commands import (
    CommandContext,
    build_registry,
    parse_command,
)
from introspect.tui.webserver import PUBLIC_BIND_WARNING, StartResult
from tests.conftest import SESSION_UUID_1

VERBS = {
    "help",
    "import",
    "reparse",
    "export",
    "status",
    "unarchive",
    "start-web",
    "stop-web",
    "quit",
}


class FakeWeb:
    """Duck-types WebServerManager for handler tests -- no real server, no socket bind."""

    def __init__(self, start_result: StartResult = StartResult.STARTED, running: bool = False):
        self._start_result = start_result
        self._running = running
        self.port = 8765

    def start(self, host: str) -> StartResult:
        if self._running:
            return StartResult.ALREADY_RUNNING
        if self._start_result is StartResult.STARTED:
            self._running = True
        return self._start_result

    def stop(self) -> bool:
        was = self._running
        self._running = False
        return was

    def local_url(self) -> str:
        return "http://127.0.0.1:8765"

    def describe(self) -> str:
        if self._running:
            return "web server: running at http://127.0.0.1:8765 (bound 127.0.0.1:8765)"
        return "web server: stopped"

    def ensure_started_local(self) -> bool:
        if self._running:
            return False
        return self.start("127.0.0.1") is StartResult.STARTED


def _factory(db_path: Path):
    engine = get_engine(db_path)
    upgrade_to_head(engine)
    return session_factory(engine)


def _ctx(
    db_path: Path,
    *,
    source_root: Path | None = None,
    web: FakeWeb | None = None,
    exit_fn=None,
) -> tuple[CommandContext, list[str]]:
    emitted: list[str] = []
    ctx = CommandContext(
        db_path=db_path,
        source_root=source_root or db_path.parent,
        session_factory=_factory(db_path),
        web=web or FakeWeb(),
        emit=emitted.append,
        exit=exit_fn or Mock(),
        registry=build_registry(),
    )
    return ctx, emitted


def _run(ctx: CommandContext, line: str) -> None:
    name, args = parse_command(line)
    ctx.registry.get(name).handler(ctx, args)


# --- Registry / parsing -------------------------------------------------------------------


def test_registry_has_every_verb() -> None:
    assert set(build_registry().names()) == VERBS


def test_slash_names_for_autocomplete() -> None:
    assert set(build_registry().slash_names()) == {f"/{v}" for v in VERBS}


def test_parse_command_splits_name_and_args() -> None:
    assert parse_command("/export abc /tmp/out") == ("export", ["abc", "/tmp/out"])
    assert parse_command("  /STATUS  ") == ("status", [])
    assert parse_command("/") == ("", [])


# --- /help --------------------------------------------------------------------------------


def test_help_lists_all_verbs(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/help")
    blob = "\n".join(emitted)
    for verb in VERBS:
        assert f"/{verb}" in blob


def test_help_command_detail_shows_usage(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/help export")
    blob = "\n".join(emitted)
    assert "/export <uuid> [path]" in blob
    assert "examples:" in blob


def test_help_unknown_command(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/help nope")
    assert any("unknown command" in line for line in emitted)


# --- /status (reuses the shared snapshot; adds web state) ---------------------------------


def test_status_emits_counts_and_web_state(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)
    _run(ctx, "/status")
    assert any(line.startswith("sessions=") for line in emitted)
    assert emitted[-1] == "web server: stopped"


# --- /export ------------------------------------------------------------------------------


def test_export_writes_reconstructed_file(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    out = tmp_path / "out.jsonl"
    ctx, emitted = _ctx(dbp)
    _run(ctx, f"/export {SESSION_UUID_1} {out}")
    assert out.exists()
    assert any("wrote" in line for line in emitted)


def test_export_usage_when_no_uuid(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/export")
    assert any("usage:" in line for line in emitted)


def test_export_unknown_session(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)
    _run(ctx, "/export not-a-real-uuid")
    assert any("export:" in line for line in emitted)


# --- /import + /reparse (the same underlying code the CLI runs) ---------------------------


def test_import_runs_in_process(tmp_path: Path, fixture_tree: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", source_root=fixture_tree)
    _run(ctx, "/import")
    assert any("status=ok" in line for line in emitted)


def test_reparse_runs_in_process(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)
    _run(ctx, "/reparse")
    assert any("records_reparsed=" in line for line in emitted)


# --- /unarchive ---------------------------------------------------------------------------


def test_unarchive_restores(tmp_path: Path, fixture_tree: Path) -> None:
    from introspect.ingest.capture import utcnow
    from introspect.models import ArchivedSession

    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    factory = _factory(dbp)
    with factory() as db:
        db.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
        db.commit()

    ctx, emitted = _ctx(dbp)
    _run(ctx, f"/unarchive {SESSION_UUID_1}")
    assert any("restored" in line for line in emitted)
    with factory() as db:
        assert db.get(ArchivedSession, SESSION_UUID_1) is None


def test_unarchive_unknown(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)
    _run(ctx, "/unarchive not-archived")
    assert any("no archived session" in line for line in emitted)


# --- web management -----------------------------------------------------------------------


def test_start_web_local(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/start-web")
    assert any("serving at http://127.0.0.1:8765" in line for line in emitted)


def test_start_web_public_prints_mandatory_warning(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/start-web public")
    assert PUBLIC_BIND_WARNING in emitted


def test_start_web_refuses_when_port_in_use(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(start_result=StartResult.PORT_IN_USE))
    _run(ctx, "/start-web")
    assert any("already in use" in line for line in emitted)


def test_start_web_already_running(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=True))
    _run(ctx, "/start-web")
    assert any("already running" in line for line in emitted)


def test_stop_web_when_running_and_not(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=True))
    _run(ctx, "/stop-web")
    assert any("stopped" in line for line in emitted)
    emitted.clear()
    _run(ctx, "/stop-web")
    assert any("no web server" in line for line in emitted)


def test_quit_invokes_exit(tmp_path: Path) -> None:
    exit_fn = Mock()
    ctx, _ = _ctx(tmp_path / "a.db", exit_fn=exit_fn)
    _run(ctx, "/quit")
    exit_fn.assert_called_once()
