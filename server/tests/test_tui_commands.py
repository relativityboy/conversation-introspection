"""Command registry + handler unit tests (§16), driven with a fake CommandContext.

Handlers are exercised directly (no running Textual app) against a real fixture DB, proving they
call the same underlying code the CLI verbs run. Web-touching handlers use a FakeWeb so no socket
is ever bound."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from introspect import changelog
from introspect import update as upd
from introspect.changelog import Entry
from introspect.cron import CrontabIO
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.run import run_import
from introspect.tui.commands import (
    CommandContext,
    _cmd_restart,
    _cmd_update,
    build_registry,
    parse_command,
)
from introspect.tui.webserver import PUBLIC_BIND_WARNING, StartResult
from tests.conftest import SESSION_UUID_1
from tests.test_cron import FakeRunner, make_exec

VERBS = {
    "help",
    "import",
    "reparse",
    "export",
    "status",
    "unarchive",
    "web",
    "cron",
    "update",
    "changelog",
    "restart",
    "quit",
}


def _fake_crontab(initial: str = "") -> CrontabIO:
    """A CrontabIO backed by an in-memory crontab -- never touches the real one."""
    return CrontabIO(runner=FakeRunner(read_text=initial))


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

    @property
    def is_running(self) -> bool:
        return self._running

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
    crontab: CrontabIO | None = None,
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
        crontab=crontab or _fake_crontab(),  # hermetic by default: empty in-memory crontab
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


def test_status_emits_counts_web_and_cron_state(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)  # default fake crontab is empty
    _run(ctx, "/status")
    assert any(line.startswith("sessions=") for line in emitted)
    assert any(line.startswith("schema: introspect-schema/") for line in emitted)
    assert "web server: stopped" in emitted
    assert emitted[-1] == "cron: not installed"  # the new trailing cron line


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


# --- /cron (marker-line ownership over the user crontab) ---------------------------------


def test_cron_status_not_installed(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    # Pinned off darwin: this test is about the status text, not the macOS notice (covered
    # separately below), and must stay deterministic regardless of the OS running the suite.
    monkeypatch.setattr(cron_mod.sys, "platform", "linux")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab("MAILTO=x\n"))
    _run(ctx, "/cron")
    assert emitted == ["cron: not installed"]


def test_cron_status_installed_shows_interval_and_line(tmp_path: Path) -> None:
    from introspect.cron import apply_install, build_line

    binary = make_exec(tmp_path)
    seeded = apply_install("", binary, 15, tmp_path / "cron.log")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab(seeded))
    _run(ctx, "/cron")
    assert any("installed@15m" in line for line in emitted)
    assert build_line(binary, 15, tmp_path / "cron.log") in emitted  # full line shown


def test_cron_install_dispatch_writes_line(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    binary = make_exec(tmp_path)
    monkeypatch.setattr(cron_mod, "resolve_binary", lambda **k: binary)
    monkeypatch.setattr(cron_mod, "DEFAULT_LOG", tmp_path / "cron.log")
    runner = FakeRunner(read_text="MAILTO=x\n")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=cron_mod.CrontabIO(runner=runner))
    _run(ctx, "/cron install 5")
    assert any("installed" in line for line in emitted)
    assert "*/5 * * * *" in runner.written[-1]
    assert runner.written[-1].startswith("MAILTO=x\n")  # unrelated line preserved


def test_cron_install_bad_minutes(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/cron install abc")
    assert any("invalid minutes" in line for line in emitted)


def test_cron_remove_dispatch(tmp_path: Path) -> None:
    from introspect.cron import CrontabIO, apply_install

    binary = make_exec(tmp_path)
    seeded = apply_install("MAILTO=x\n", binary, 15, tmp_path / "cron.log")
    runner = FakeRunner(read_text=seeded)
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=CrontabIO(runner=runner))
    _run(ctx, "/cron remove")
    assert any("removed" in line for line in emitted)
    assert runner.written[-1] == "MAILTO=x\n"  # byte-for-byte back to original


def test_cron_remove_when_absent(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab("MAILTO=x\n"))
    _run(ctx, "/cron remove")
    assert any("nothing to remove" in line for line in emitted)


def test_cron_unknown_subcommand(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/cron frobnicate")
    assert any("usage:" in line for line in emitted)


def test_cron_help_has_silent_caveat(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/help cron")
    blob = "\n".join(emitted).lower()
    assert "crontab" in blob
    assert "silent" in blob  # the "cron runs silently" caveat is present


def test_cron_help_has_macos_permission_caveat(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/help cron")
    blob = "\n".join(emitted).lower()
    assert "permission" in blob  # the macOS TCC-dialog caveat is present (distinct from "silent")


# --- /cron: macOS TCC-permission-dialog forewarning ----------------------------------------
# `crontab` (even `-l`) triggers an unwarned macOS permission dialog the first time a terminal
# app invokes it; these tests pin `sys.platform` explicitly so behavior is deterministic
# regardless of which OS actually runs the suite.


def test_cron_status_shows_macos_notice_on_darwin(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    monkeypatch.setattr(cron_mod.sys, "platform", "darwin")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab("MAILTO=x\n"))
    _run(ctx, "/cron")
    assert emitted == [cron_mod.MACOS_PERMISSION_NOTICE, "cron: not installed"]


def test_cron_status_no_macos_notice_off_darwin(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    monkeypatch.setattr(cron_mod.sys, "platform", "linux")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab("MAILTO=x\n"))
    _run(ctx, "/cron")
    assert cron_mod.MACOS_PERMISSION_NOTICE not in emitted


def test_cron_install_shows_macos_notice_once_despite_read_and_write(
    tmp_path: Path, monkeypatch
) -> None:
    import introspect.cron as cron_mod

    monkeypatch.setattr(cron_mod.sys, "platform", "darwin")
    binary = make_exec(tmp_path)
    monkeypatch.setattr(cron_mod, "resolve_binary", lambda **k: binary)
    monkeypatch.setattr(cron_mod, "DEFAULT_LOG", tmp_path / "cron.log")
    runner = FakeRunner(read_text="MAILTO=x\n")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=cron_mod.CrontabIO(runner=runner))
    _run(ctx, "/cron install 5")
    # `install` issues a read AND a write subprocess call -- the notice must still appear once.
    assert emitted.count(cron_mod.MACOS_PERMISSION_NOTICE) == 1


def test_cron_install_bad_minutes_shows_no_macos_notice(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    # No crontab call is ever made on this path (validation fails first) -- no notice either.
    monkeypatch.setattr(cron_mod.sys, "platform", "darwin")
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/cron install abc")
    assert cron_mod.MACOS_PERMISSION_NOTICE not in emitted


def test_cron_remove_shows_macos_notice_on_darwin(tmp_path: Path, monkeypatch) -> None:
    import introspect.cron as cron_mod

    monkeypatch.setattr(cron_mod.sys, "platform", "darwin")
    ctx, emitted = _ctx(tmp_path / "a.db", crontab=_fake_crontab("MAILTO=x\n"))
    _run(ctx, "/cron remove")
    assert cron_mod.MACOS_PERMISSION_NOTICE in emitted


# --- /update / /restart (TUI consent idiom: check -> show + hint; 'yes' -> apply) ---------
# Two-step by design (spec §5 adapted for the TUI): a bare /update never applies -- it always
# monkeypatches `introspect.tui.commands.upd` (the same module object `commands.py` imports
# as `upd`), so patching it here patches what the handler sees.


class RecordingWeb(FakeWeb):
    """FakeWeb that also records whether stop()/start() were invoked -- proves /update's web
    bounce actually calls both, not just one."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stopped = False
        self.started = False

    def stop(self) -> bool:
        self.stopped = True
        return super().stop()

    def start(self, host: str) -> StartResult:
        self.started = True
        return super().start(host)


def test_update_check_shows_changelist_and_hint(tmp_path: Path, monkeypatch) -> None:
    entry = Entry(version="1.2.0", date="2026-08-08", bullets=("New thing.",))
    monkeypatch.setattr(upd, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        upd,
        "check",
        lambda root: upd.UpdateCheck(upd.UpdateState.BEHIND, "1.1.0", "1.2.0", (entry,)),
    )
    monkeypatch.setattr(upd, "preflight_problems", lambda root: [])

    def _apply_should_not_be_called(root, emit, update_script=None):
        raise AssertionError("apply must not be called by a bare /update")

    monkeypatch.setattr(upd, "apply", _apply_should_not_be_called)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _cmd_update(ctx, [])
    out = "\n".join(emitted)
    assert "1.2.0" in out and "New thing." in out
    assert "/update yes" in out  # the consent hint


def test_update_yes_applies_bounces_web_and_hints_restart(tmp_path: Path, monkeypatch) -> None:
    entry = Entry(version="1.2.0", date="2026-08-08", bullets=("New thing.",))
    monkeypatch.setattr(upd, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        upd,
        "check",
        lambda root: upd.UpdateCheck(upd.UpdateState.BEHIND, "1.1.0", "1.2.0", (entry,)),
    )
    monkeypatch.setattr(upd, "preflight_problems", lambda root: [])
    monkeypatch.setattr(
        upd,
        "apply",
        lambda root, emit, update_script=None: upd.ApplyResult(ok=True, server_changed=True),
    )
    web = RecordingWeb(running=True)
    ctx, emitted = _ctx(tmp_path / "a.db", web=web)
    _cmd_update(ctx, ["yes"])
    out = "\n".join(emitted)
    assert web.stopped and web.started  # bounced
    assert "/restart" in out  # server changed -> restart hint


def test_update_yes_web_only_reports_updated(tmp_path: Path, monkeypatch) -> None:
    entry = Entry(version="1.2.0", date="2026-08-08", bullets=("New thing.",))
    monkeypatch.setattr(upd, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        upd,
        "check",
        lambda root: upd.UpdateCheck(upd.UpdateState.BEHIND, "1.1.0", "1.2.0", (entry,)),
    )
    monkeypatch.setattr(upd, "preflight_problems", lambda root: [])
    monkeypatch.setattr(
        upd,
        "apply",
        lambda root, emit, update_script=None: upd.ApplyResult(ok=True, server_changed=False),
    )
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=False))
    _cmd_update(ctx, ["yes"])
    assert any("updated to 1.2.0" in line for line in emitted)
    assert not any("/restart" in line for line in emitted)


def test_update_up_to_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(upd, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        upd,
        "check",
        lambda root: upd.UpdateCheck(upd.UpdateState.UP_TO_DATE, "1.1.0", "1.1.0", ()),
    )
    ctx, emitted = _ctx(tmp_path / "a.db")
    _cmd_update(ctx, [])
    assert any("already up to date (1.1.0)" in line for line in emitted)
    assert any("this check compares versions only" in line for line in emitted)
    assert any("./update.sh to rebuild" in line for line in emitted)


def test_update_preflight_problems_block_apply(tmp_path: Path, monkeypatch) -> None:
    entry = Entry(version="1.2.0", date="2026-08-08", bullets=("New thing.",))
    monkeypatch.setattr(upd, "find_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(
        upd,
        "check",
        lambda root: upd.UpdateCheck(upd.UpdateState.BEHIND, "1.1.0", "1.2.0", (entry,)),
    )
    monkeypatch.setattr(
        upd,
        "preflight_problems",
        lambda root: ["working tree has uncommitted changes to tracked files"],
    )

    def _apply_should_not_be_called(root, emit, update_script=None):
        raise AssertionError("apply must not be called when preflight problems are present")

    monkeypatch.setattr(upd, "apply", _apply_should_not_be_called)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _cmd_update(ctx, ["yes"])
    assert any("uncommitted" in line for line in emitted)


def test_restart_exits_with_marker(tmp_path: Path) -> None:
    exit_calls: list[object] = []
    ctx, _ = _ctx(tmp_path / "a.db", exit_fn=exit_calls.append)
    _cmd_restart(ctx, [])
    assert exit_calls == ["restart"]  # ctx.exit called with the marker


def test_status_includes_version_line(tmp_path: Path, fixture_tree: Path, monkeypatch) -> None:
    monkeypatch.setattr(changelog, "app_version", lambda: "1.2.0")
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    ctx, emitted = _ctx(dbp)
    _run(ctx, "/status")
    assert any(line == "version: 1.2.0" for line in emitted)


# --- web management -----------------------------------------------------------------------


def test_web_bare_reports_status(tmp_path: Path) -> None:
    # /web mirrors /cron: no subcommand = status.
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/web")
    assert any(line == "web server: stopped" for line in emitted)


def test_web_status_while_running(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=True))
    _run(ctx, "/web status")
    assert any("running at http://127.0.0.1:8765" in line for line in emitted)


def test_web_start_local(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/web start")
    assert any("serving at http://127.0.0.1:8765" in line for line in emitted)


def test_web_start_public_prints_mandatory_warning(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/web start public")
    assert PUBLIC_BIND_WARNING in emitted


def test_web_start_refuses_when_port_in_use(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(start_result=StartResult.PORT_IN_USE))
    _run(ctx, "/web start")
    assert any("already in use" in line for line in emitted)


def test_web_start_already_running(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=True))
    _run(ctx, "/web start")
    assert any("already running" in line for line in emitted)


def test_web_stop_when_running_and_not(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb(running=True))
    _run(ctx, "/web stop")
    assert any("stopped" in line for line in emitted)
    emitted.clear()
    _run(ctx, "/web stop")
    assert any("no web server" in line for line in emitted)


def test_web_unknown_subcommand_prints_usage(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/web bogus")
    assert any("usage: /web" in line for line in emitted)


def test_web_start_rejects_unknown_arg(tmp_path: Path) -> None:
    ctx, emitted = _ctx(tmp_path / "a.db", web=FakeWeb())
    _run(ctx, "/web start bogus")
    assert any("usage: /web" in line for line in emitted)
    assert not any("serving at" in line for line in emitted)


def _stub_entries() -> list[Entry]:
    return [
        Entry("1.2.0", "2026-08-08", ("Versions everywhere.", "`/update` in the TUI.")),
        Entry("1.1.0", "2026-08-07", ("Authorship labels.",)),
    ]


def test_changelog_bare_shows_latest_entry_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(changelog, "app_entries", _stub_entries)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/changelog")
    blob = "\n".join(emitted)
    assert "## 1.2.0 — 2026-08-08" in blob
    assert "- Versions everywhere." in blob
    assert "1.1.0" not in blob  # older entries stay hidden without 'all'
    assert any("/changelog all" in line for line in emitted)  # hint at the rest


def test_changelog_all_shows_full_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(changelog, "app_entries", _stub_entries)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/changelog all")
    blob = "\n".join(emitted)
    assert "## 1.2.0 — 2026-08-08" in blob
    assert "## 1.1.0 — 2026-08-07" in blob
    assert "- Authorship labels." in blob


def test_changelog_unknown_arg_prints_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(changelog, "app_entries", _stub_entries)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/changelog bogus")
    assert any("usage: /changelog" in line for line in emitted)


def test_changelog_unreadable_reports_cleanly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(changelog, "app_entries", lambda: None)
    ctx, emitted = _ctx(tmp_path / "a.db")
    _run(ctx, "/changelog")
    assert any("no readable CHANGELOG" in line for line in emitted)


def test_quit_invokes_exit(tmp_path: Path) -> None:
    exit_fn = Mock()
    ctx, _ = _ctx(tmp_path / "a.db", exit_fn=exit_fn)
    _run(ctx, "/quit")
    exit_fn.assert_called_once()
