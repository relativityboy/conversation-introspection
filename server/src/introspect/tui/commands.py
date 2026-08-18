"""Slash-command registry and handlers for the TUI (§16).

A registry pattern (step 2 adds more commands cheaply) mapping a bare command name to a
:class:`Command` carrying its help text and a handler. Handlers receive a :class:`CommandContext`
-- the app's DB/web/emit seam -- and are the SAME underlying code the CLI verbs run
(:func:`run_import`, :func:`reparse_all`, :func:`export_session_to`, the status snapshot, the
unarchive delete), never subprocesses. ``import``/``reparse`` are marked ``background`` so the
app runs them in a worker thread and the UI stays live.

No ``textual`` import here: everything is a plain callable over ``CommandContext``, so the
registry, dispatch, and every handler are unit-testable with a fake context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from introspect import changelog, cron, deletion, exclusion, skills
from introspect import update as upd
from introspect.db import get_engine
from introspect.cron import CrontabIO
from introspect.export import (
    SessionNotFoundError,
    TranscriptNotFoundError,
    export_session_to,
)
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import DbOpenError, _acquire_lock, _release_lock, run_import
from introspect.models import ArchivedSession
from introspect.status import collect_status, counts_line, last_run_line, schema_line
from introspect.tui.webserver import (
    LOCAL_HOST,
    PUBLIC_BIND_WARNING,
    PUBLIC_HOST,
    StartResult,
    WebServerManager,
)


@dataclass
class CommandContext:
    """Everything a handler needs from the running app, and nothing more.

    ``emit`` appends one line to the log area; on the background-worker path the app supplies a
    thread-safe ``emit`` (marshalled to the UI thread). ``exit`` ends the app (``/quit``,
    ``/restart``) -- it binds Textual's ``App.exit(result=None)``, so it takes an optional
    positional result (``/restart`` passes the ``"restart"`` marker).
    """

    db_path: Path
    source_root: Path
    session_factory: Callable[[], Session]
    web: WebServerManager
    emit: Callable[[str], None]
    exit: Callable[..., None]
    registry: "CommandRegistry"
    # The user-crontab edge for /cron. A field (like `web`) so tests inject a fake and no test
    # ever shells out to the real crontab; the app supplies one real instance.
    crontab: CrontabIO = field(default_factory=CrontabIO)
    # Where /skill installs repo skills. A field for the same hermetic reason: tests inject a
    # tmp dir and never touch the real ~/.claude/skills.
    skills_home: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "skills"
    )


@dataclass
class Command:
    name: str  # bare name, no leading slash (e.g. "export")
    summary: str  # one-liner for `/help`
    usage: str  # e.g. "/export <uuid> [path]"
    long_help: str  # multi-line body for `/help <command>`
    handler: Callable[[CommandContext, list[str]], None]
    examples: list[str] = field(default_factory=list)
    background: bool = False  # run in a worker thread (long-running verbs)


class CommandRegistry:
    """Insertion-ordered registry of slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        return list(self._commands.values())

    def names(self) -> list[str]:
        return list(self._commands)

    def slash_names(self) -> list[str]:
        """`/name` forms, for the input autocomplete suggester."""
        return [f"/{name}" for name in self._commands]


def parse_command(text: str) -> tuple[str, list[str]]:
    """Split a slash-command line into ``(name, args)``. ``name`` is lower-cased, slash-stripped.

    Precondition: ``text`` (stripped) starts with ``/`` -- the app routes non-slash input to
    search before calling here. A bare ``/`` yields ``("", [])`` (an unknown command downstream).
    """
    parts = text.strip()[1:].split()
    if not parts:
        return "", []
    return parts[0].lower(), parts[1:]


# --- Handlers -----------------------------------------------------------------------------


def _cmd_help(ctx: CommandContext, args: list[str]) -> None:
    if args:
        command = ctx.registry.get(args[0].lstrip("/").lower())
        if command is None:
            ctx.emit(f"unknown command: /{args[0].lstrip('/')} -- type /help")
            return
        ctx.emit(f"/{command.name} -- {command.summary}")
        ctx.emit(f"usage: {command.usage}")
        for line in command.long_help.splitlines():
            ctx.emit(line)
        if command.examples:
            ctx.emit("examples:")
            for example in command.examples:
                ctx.emit(f"  {example}")
        return
    ctx.emit("commands:")
    for command in ctx.registry.all():
        ctx.emit(f"  /{command.name:<11}{command.summary}")
    ctx.emit("")
    ctx.emit("search: type text with NO leading '/' to search the archive.")
    ctx.emit("  Up/Down navigate results; Enter or Right opens the best-matching message")
    ctx.emit("  in your browser. Either auto-starts the web server on 127.0.0.1 if it is")
    ctx.emit("  stopped (a browser launch needs a server).")
    ctx.emit("  Default searches the CHAT only (you and Claude talking). Widen with flags")
    ctx.emit("  in the search text: --agents (subagent transcripts), --system (harness")
    ctx.emit("  records), --all (everything).")
    ctx.emit("panels: drag the divider with the mouse, or alt+up / alt+down")
    ctx.emit("  (ctrl+shift+up/down also works) to resize the log area.")
    ctx.emit("/help <command> for a command's details, examples, and caveats.")


def _cmd_import(ctx: CommandContext, args: list[str]) -> None:
    ctx.emit("import: starting...")
    try:
        summary = run_import(ctx.db_path, ctx.source_root, trigger="cli")
    except DbOpenError as exc:
        ctx.emit(f"import: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 -- mid-run fatal surfaces as one clean line
        ctx.emit(f"import: failed: {exc}")
        return
    ctx.emit(
        f"import: files={summary.files_seen} records={summary.records_added} "
        f"dupes={summary.records_skipped_duplicate} anomalies={summary.anomaly_count} "
        f"gone={summary.gone_flipped} status={summary.status}"
    )


def _cmd_reparse(ctx: CommandContext, args: list[str]) -> None:
    # Take the SAME advisory lock as `run_import`/CLI reparse so a concurrent import/reparse
    # pair can never race (mirrors cli._cmd_reparse -- the private lock helpers are reused for
    # a smaller diff than lifting them to a shared module).
    lock_fh = _acquire_lock(ctx.db_path.parent / "import.lock")
    if lock_fh is None:
        ctx.emit("reparse: another import/reparse is already running")
        return
    try:
        with ctx.session_factory() as db:
            stats = reparse_all(db)
    except Exception as exc:  # noqa: BLE001 -- internal failure: one clean line
        ctx.emit(f"reparse: failed: {exc}")
        return
    finally:
        _release_lock(lock_fh)
    ctx.emit(
        f"reparse: records_reparsed={stats.records_reparsed} "
        f"anomalies_before={stats.anomalies_before} anomalies_after={stats.anomalies_after}"
    )


def _cmd_export(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        ctx.emit("usage: /export <uuid> [path]")
        return
    session_uuid = args[0]
    out_path = Path(args[1]) if len(args) > 1 else Path.cwd() / f"{session_uuid}.jsonl"
    try:
        with ctx.session_factory() as db:
            export_session_to(db, session_uuid, out_path)
    except (SessionNotFoundError, TranscriptNotFoundError) as exc:
        ctx.emit(f"export: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 -- internal failure: one clean line
        ctx.emit(f"export: failed: {exc}")
        return
    ctx.emit(f"export: wrote {out_path}")


def _cmd_status(ctx: CommandContext, args: list[str]) -> None:
    ctx.emit(f"version: {changelog.app_version()}")
    with ctx.session_factory() as db:
        snap = collect_status(db)
    ctx.emit(counts_line(snap))
    ctx.emit(last_run_line(snap))
    ctx.emit(schema_line(snap))
    ctx.emit(ctx.web.describe())
    try:
        ctx.emit(cron.status_line(cron.status(ctx.crontab)))
    except cron.CronError as exc:
        # Reading the crontab is best-effort here -- an unreadable crontab must not sink /status.
        ctx.emit(f"cron: unavailable ({exc})")


def _cmd_cron(ctx: CommandContext, args: list[str]) -> None:
    sub = args[0].lower() if args else "status"
    if sub == "status":
        _cron_show_status(ctx)
    elif sub == "install":
        _cron_install(ctx, args[1:])
    elif sub == "remove":
        _cron_remove(ctx)
    else:
        ctx.emit("usage: /cron [install [minutes] | remove]")


def _cron_show_status(ctx: CommandContext) -> None:
    notice = cron.macos_permission_notice()
    if notice is not None:
        ctx.emit(notice)
    try:
        st = cron.status(ctx.crontab)
    except cron.CronError as exc:
        ctx.emit(f"cron: {exc}")
        return
    ctx.emit(cron.status_line(st))
    if st.line is not None:
        ctx.emit(st.line)


def _cron_install(ctx: CommandContext, rest: list[str]) -> None:
    minutes = 15
    if rest:
        try:
            minutes = int(rest[0])
        except ValueError:
            ctx.emit(f"cron: invalid minutes '{rest[0]}' -- must be an integer 1-60")
            return
    notice = cron.macos_permission_notice()
    if notice is not None:
        ctx.emit(notice)
    try:
        st = cron.install(ctx.crontab, minutes=minutes)
    except cron.CronError as exc:
        ctx.emit(f"cron: {exc}")
        return
    ctx.emit(f"cron: installed ({cron.status_line(st).removeprefix('cron: ')})")
    ctx.emit(st.line)


def _cron_remove(ctx: CommandContext) -> None:
    notice = cron.macos_permission_notice()
    if notice is not None:
        ctx.emit(notice)
    try:
        removed = cron.remove(ctx.crontab)
    except cron.CronError as exc:
        ctx.emit(f"cron: {exc}")
        return
    ctx.emit("cron: removed" if removed else "cron: nothing to remove (not installed)")


def _cmd_unarchive(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        ctx.emit("usage: /unarchive <uuid>")
        return
    session_uuid = args[0]
    with ctx.session_factory() as db:
        row = db.get(ArchivedSession, session_uuid)
        if row is None:
            ctx.emit(f"unarchive: no archived session {session_uuid}")
            return
        db.delete(row)
        db.commit()
    ctx.emit(f"unarchive: restored {session_uuid}")


def _cmd_web(ctx: CommandContext, args: list[str]) -> None:
    # Subcommand shape mirrors /cron: bare name = status.
    sub = args[0].lower() if args else "status"
    if sub == "status":
        ctx.emit(ctx.web.describe())
    elif sub == "start":
        _web_start(ctx, args[1:])
    elif sub == "stop":
        _web_stop(ctx)
    else:
        ctx.emit("usage: /web [start [public] | stop | status]")


def _web_start(ctx: CommandContext, rest: list[str]) -> None:
    public = len(rest) == 1 and rest[0].lower() == "public"
    if rest and not public:
        ctx.emit("usage: /web [start [public] | stop | status]")
        return
    if public:
        # Mandatory warning BEFORE the bind attempt (§16) -- the user sees the risk regardless
        # of whether the start then succeeds.
        ctx.emit(PUBLIC_BIND_WARNING)
    host = PUBLIC_HOST if public else LOCAL_HOST
    result = ctx.web.start(host)
    if result is StartResult.ALREADY_RUNNING:
        ctx.emit(f"web: already running at {ctx.web.local_url()}")
    elif result is StartResult.PORT_IN_USE:
        ctx.emit(
            f"web: cannot start -- port {ctx.web.port} is already in use by another "
            f"process. Stop it, or free the port, and try again."
        )
    elif result is StartResult.FAILED:
        ctx.emit(f"web: failed to bind {host}:{ctx.web.port}")
    else:
        ctx.emit(f"web: serving at {ctx.web.local_url()} (bound {host}:{ctx.web.port})")


def _web_stop(ctx: CommandContext) -> None:
    if ctx.web.stop():
        ctx.emit("web: server stopped")
    else:
        ctx.emit("web: no web server was running")


def _cmd_update(ctx: CommandContext, args: list[str]) -> None:
    # TUI consent idiom (spec §5 adapted): a bare /update only checks and shows the changelist,
    # hinting at '/update yes' -- it NEVER applies. Only args == ["yes"] reaches upd.apply below.
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
        ctx.emit(
            "(this check compares versions only -- after a manual git pull, run "
            "./update.sh to rebuild)"
        )
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
            ctx.emit(f"web server did not restart cleanly ({started.name}) -- /web start to retry")
    if result.server_changed:
        ctx.emit("server code changed -- type /restart to relaunch the TUI on the new code")
    else:
        ctx.emit(f"updated to {chk.remote_version}")


def _cmd_changelog(ctx: CommandContext, args: list[str]) -> None:
    sub = args[0].lower() if args else ""
    if args and (sub != "all" or len(args) > 1):
        ctx.emit("usage: /changelog [all]")
        return
    entries = changelog.app_entries()
    if entries is None:
        ctx.emit("changelog: no readable CHANGELOG.md found")
        return
    shown = entries if sub == "all" else entries[:1]
    for entry in shown:
        ctx.emit(f"## {entry.version} — {entry.date}")
        for bullet in entry.bullets:
            ctx.emit(f"- {bullet}")
    if sub != "all" and len(entries) > 1:
        ctx.emit(f"({len(entries) - 1} older entries -- '/changelog all' for the full history)")


def _cmd_skill(ctx: CommandContext, args: list[str]) -> None:
    sub = args[0].lower() if args else "status"
    if sub not in ("status", "install") or len(args) > 1:
        ctx.emit("usage: /skill [install | status]")
        return
    root = upd.find_repo_root()
    if root is None:
        ctx.emit("skill: requires a repo checkout (no .git found)")
        return
    if sub == "status":
        status = skills.skills_status(root, ctx.skills_home)
        if not status:
            ctx.emit("skill: no skills shipped in this checkout")
            return
        for name, state in status.items():
            ctx.emit(f"skill: {name}: {state}")
        if any(state != skills.STATUS_CURRENT for state in status.values()):
            ctx.emit("'/skill install' to install/update into " + str(ctx.skills_home))
        return
    outcomes = skills.install_skills(root, ctx.skills_home)
    if not outcomes:
        ctx.emit("skill: no skills shipped in this checkout")
        return
    for name, outcome in outcomes.items():
        ctx.emit(f"skill: {name}: {outcome} -> {ctx.skills_home / name / 'SKILL.md'}")


def _cmd_exclude(ctx: CommandContext, args: list[str]) -> None:
    sub = args[0].lower() if args else "list"
    if sub == "list" and len(args) <= 1:
        _exclude_list(ctx)
    elif sub == "add" and len(args) >= 2:
        _exclude_add(ctx, args[1], " ".join(args[2:]) or None)
    elif sub == "remove" and len(args) == 2:
        _exclude_remove(ctx, args[1])
    else:
        ctx.emit("usage: /exclude [add <path-or-slug> [reason...] | remove <slug> | list]")


def _exclude_list(ctx: CommandContext) -> None:
    with ctx.session_factory() as db:
        projects, sessions = exclusion.list_exclusions(db)
        if not projects and not sessions:
            ctx.emit("exclude: no projects are excluded")
            return
        for row in projects:
            suffix = f" -- {row.reason}" if row.reason else ""
            ctx.emit(f"exclude: {row.dir_slug}{suffix}")
        for srow in sessions:
            suffix = f" -- {srow.reason}" if srow.reason else ""
            ctx.emit(f"exclude: session {srow.session_uuid}{suffix}")


def _exclude_add(ctx: CommandContext, target: str, reason: str | None) -> None:
    with ctx.session_factory() as db:
        outcome = exclusion.add_exclusion(db, target, reason)
    if outcome.already_excluded:
        ctx.emit(f"exclude: {outcome.slug} is already excluded")
        return
    if outcome.kind == "session":
        ctx.emit(f"excluded session: {outcome.slug} -- re-import forbidden")
        return
    ctx.emit(f"excluded: {outcome.slug}")
    ctx.emit("this project will never be captured (imports skip its directory entirely)")
    if outcome.prior_sessions:
        # Prevention-only honesty (spec 2026-08-17 §2): exclusion walls off the future;
        # anything already captured stays until the deletion tool (phase B) repairs it.
        ctx.emit(
            f"note: {outcome.prior_sessions} session(s) from this project were already "
            f"captured and remain in the archive -- exclusion prevents future capture "
            f"only; deletion is the repair tool for what already landed"
        )


def _exclude_remove(ctx: CommandContext, target: str) -> None:
    with ctx.session_factory() as db:
        slug, removed = exclusion.remove_exclusion(db, target)
    if not removed:
        ctx.emit(f"exclude: {slug} was not excluded")
        return
    ctx.emit(f"exclude: {slug} is no longer excluded (future imports will capture it)")


def _cmd_delete(ctx: CommandContext, args: list[str]) -> None:
    if not args:
        ctx.emit(
            "usage: /delete <uuid-or-slug>            (preview -- deletes nothing)"
        )
        ctx.emit("       /delete <target> yes [reason...]  (the irreversible act)")
        ctx.emit("       /delete <target> backups yes      (also scrub backup copies)")
        ctx.emit("       /delete <uuid> forbid yes          (forbid re-import)")
        return
    target = args[0]
    kind = "session" if exclusion.is_session_uuid(target) else "project"
    rest = args[1:]
    if not rest:
        _delete_preview(ctx, kind, target)
    elif rest[0].lower() == "yes":
        _delete_confirm(ctx, kind, target, " ".join(rest[1:]) or None)
    elif rest[:2] == ["forbid", "yes"] and kind == "session":
        with ctx.session_factory() as db:
            added = deletion.forbid_reimport(db, target, reason="deleted; owner forbade re-import")
        ctx.emit(
            f"delete: {target} -- re-import forbidden"
            if added
            else f"delete: {target} re-import was already forbidden"
        )
        ctx.emit("('/exclude remove <uuid>' re-allows it later if you change your mind)")
    elif rest[:2] == ["backups", "yes"]:
        _delete_backups(ctx, kind, target)
    else:
        ctx.emit("usage: /delete <target> [yes [reason...] | backups yes | forbid yes]")


def _delete_preview(ctx: CommandContext, kind: str, target: str) -> None:
    with ctx.session_factory() as db:
        pv = (
            deletion.preview_session(db, target)
            if kind == "session"
            else deletion.preview_project(db, exclusion.resolve_slug(target))
        )
    if pv is None:
        ctx.emit(f"delete: {kind} {target} not found -- nothing to delete")
        return
    ctx.emit(deletion.DELETION_WARNING)
    label = f" ({pv.label})" if pv.label and pv.label != pv.target else ""
    ctx.emit(
        f"would delete: {pv.kind} {pv.target}{label} -- "
        f"{pv.sessions} session(s), {pv.records} archived record(s)"
    )
    if pv.source_paths_on_disk:
        ctx.emit(
            f"note: {len(pv.source_paths_on_disk)} source file(s) still exist on disk -- "
            f"without forbidding re-import, the next import will re-capture this"
        )
    n_backups = len(deletion.list_backup_dbs(ctx.db_path))
    if n_backups:
        ctx.emit(f"note: {n_backups} backup DB cop(ies) also hold this data (separate ask)")
    ctx.emit(f"to proceed: /delete {target} yes [reason...]   (reason goes in the ledger)")


def _delete_confirm(ctx: CommandContext, kind: str, target: str, reason: str | None) -> None:
    with ctx.session_factory() as db:
        outcome = (
            deletion.delete_session(db, target, reason)
            if kind == "session"
            else deletion.delete_project(db, exclusion.resolve_slug(target), reason)
        )
    if outcome is None:
        ctx.emit(f"delete: {kind} {target} not found -- nothing deleted")
        return
    ctx.emit(
        f"deleted: {outcome.kind} {outcome.target} -- {outcome.sessions_deleted} "
        f"session(s), {outcome.records_deleted} record(s); ledger updated"
        + (f" (reason: {reason})" if reason else " (no reason given)")
    )
    ctx.emit("reclaiming space (VACUUM) -- this can take a while on a large archive...")
    deletion.finalize_scrub(get_engine(ctx.db_path))
    ctx.emit("scrub complete (WAL checkpointed, space reclaimed)")

    # The two additional asks (never automatic):
    if outcome.source_paths_on_disk:
        ctx.emit(
            f"NOT touched: {len(outcome.source_paths_on_disk)} live source file(s) on disk "
            f"-- the next import WILL re-capture this unless forbidden:"
        )
        for p in outcome.source_paths_on_disk:
            ctx.emit(f"  {p}")
        if kind == "session":
            ctx.emit(f"forbid re-import: /delete {target} forbid yes")
        else:
            ctx.emit(f"forbid re-import: /exclude add {target}")
    n_backups = len(deletion.list_backup_dbs(ctx.db_path))
    if n_backups:
        ctx.emit(
            f"NOT touched: {n_backups} backup DB cop(ies) under backups/ still hold this "
            f"data -- scrub them too: /delete {target} backups yes"
        )


def _delete_backups(ctx: CommandContext, kind: str, target: str) -> None:
    resolved = target if kind == "session" else exclusion.resolve_slug(target)
    results = deletion.scrub_backups(ctx.db_path, kind, resolved, reason=None)
    if not results:
        ctx.emit("delete: no backup DB copies found under backups/")
        return
    for path, was_present in results:
        state = "scrubbed" if was_present else "did not contain the target"
        ctx.emit(f"backup {path.name}: {state}")


def _cmd_restart(ctx: CommandContext, args: list[str]) -> None:
    # No same-process reload: exits with the "restart" marker so `_cmd_tui` (cli.py) can
    # `os.execvp` a fresh process AFTER app.run() returns -- see that module for why.
    ctx.exit("restart")


def _cmd_quit(ctx: CommandContext, args: list[str]) -> None:
    ctx.exit()


def build_registry() -> CommandRegistry:
    """Build the §16 step-1 command set. Order here is the order `/help` lists them."""
    registry = CommandRegistry()
    registry.register(
        Command(
            name="help",
            summary="list commands, or explain one",
            usage="/help [command]",
            long_help=(
                "With no argument, lists every command with its one-line summary and the\n"
                "search key bindings. With a command name, prints that command's usage,\n"
                "full description, examples, and caveats."
            ),
            examples=["/help", "/help export", "/help web"],
            handler=_cmd_help,
        )
    )
    registry.register(
        Command(
            name="import",
            summary="ingest new/changed transcripts",
            usage="/import",
            long_help=(
                "Runs the same import the CLI/cron entry point runs (in-process, not a\n"
                "subprocess), taking the shared advisory lock. Runs in a background worker so\n"
                "the UI stays live; the result summary streams into the log when it finishes.\n"
                "Caveat: if a cron import already holds the lock, this reports 'already_running'\n"
                "and does nothing -- that is a no-op, not a failure."
            ),
            examples=["/import"],
            handler=_cmd_import,
            background=True,
        )
    )
    registry.register(
        Command(
            name="reparse",
            summary="rebuild interpretation from stored raw bytes",
            usage="/reparse",
            long_help=(
                "Re-runs interpretation over the archived raw lines (no source files needed).\n"
                "Takes the same advisory lock as import; runs in a background worker. Reports\n"
                "records reparsed and the anomaly counts before/after -- the drift-fix loop.\n"
                "Caveat: if an import/reparse already holds the lock, this refuses cleanly."
            ),
            examples=["/reparse"],
            handler=_cmd_reparse,
            background=True,
        )
    )
    registry.register(
        Command(
            name="export",
            summary="reconstruct a session's byte-faithful .jsonl",
            usage="/export <uuid> [path]",
            long_help=(
                "Reconstructs the session's transcript from the archive (byte-for-byte). With\n"
                "no path, writes <uuid>.jsonl into the current working directory.\n"
                "Caveat: an unknown session uuid reports a not-found message and writes nothing."
            ),
            examples=[
                "/export 11111111-1111-1111-1111-111111111111",
                "/export 11111111-1111-1111-1111-111111111111 /tmp/out.jsonl",
            ],
            handler=_cmd_export,
        )
    )
    registry.register(
        Command(
            name="status",
            summary="archive counts, last import, web-server state",
            usage="/status",
            long_help=(
                "Prints the same headline counts as `introspect status` (sessions, archived,\n"
                "files, records, anomalies by severity, last import run), then a line for the\n"
                "in-process web server (stopped, or running with its url:port).\n"
                "Note: 'archived' is an aggregate count only -- no archived identities are shown."
            ),
            examples=["/status"],
            handler=_cmd_status,
        )
    )
    registry.register(
        Command(
            name="unarchive",
            summary="restore an archived session (by uuid)",
            usage="/unarchive <uuid>",
            long_help=(
                "Removes a session from the archived set so it becomes readable again. The uuid\n"
                "must be known out-of-band: by design nothing lists archived sessions.\n"
                "Caveat: a uuid that is unknown or simply not archived reports a message and\n"
                "changes nothing."
            ),
            examples=["/unarchive 11111111-1111-1111-1111-111111111111"],
            handler=_cmd_unarchive,
        )
    )
    registry.register(
        Command(
            name="web",
            summary="web server: status, start (add 'public' to expose), stop",
            usage="/web [start [public] | stop | status]",
            long_help=(
                "With no argument, reports the web server's state (same line as /status shows).\n"
                "'start' runs the archive web server in-process on 127.0.0.1:8765; add 'public'\n"
                "to bind 0.0.0.0 instead. 'stop' stops the server the TUI started, if any --\n"
                "exiting the TUI stops it too.\n"
                "CAVEAT (public): the archive has NO authentication -- a public bind makes every\n"
                "captured message readable by anyone on your network. A mandatory warning prints\n"
                "before the bind. If the port is already held by another process, start refuses\n"
                "cleanly (no crash). The server also auto-starts on 127.0.0.1 when you open a\n"
                "search result in the browser."
            ),
            examples=["/web", "/web start", "/web start public", "/web stop"],
            handler=_cmd_web,
        )
    )
    registry.register(
        Command(
            name="cron",
            summary="schedule (or unschedule) periodic imports via the user crontab",
            usage="/cron [install [minutes] | remove]",
            long_help=(
                "With no argument, reports whether the managed import job is scheduled and, if\n"
                "so, its interval and the exact crontab line. 'install' adds or REPLACES a single\n"
                "line that runs `introspect import` every N minutes (default 15; valid 1-60); it\n"
                "never creates a second line. 'remove' deletes only that line, leaving every other\n"
                "crontab entry byte-for-byte untouched.\n"
                "CAVEAT: this edits your user crontab (the same one `crontab -e` shows). cron runs\n"
                "the job SILENTLY -- on macOS there is no prompt and no notification; output is\n"
                "appended to ~/.conversation-introspection/cron.log. Run /cron with no argument\n"
                "(or `introspect cron status`) to confirm it is scheduled.\n"
                "macOS CAVEAT: the FIRST time this command runs `crontab` (status, install, or\n"
                "remove alike -- even a read-only `crontab -l` counts) macOS shows a one-time\n"
                "permission dialog asking to let your terminal manage scheduled jobs. That's the\n"
                "OS, not us; declining it makes cron commands fail with a permission error."
            ),
            examples=["/cron", "/cron install", "/cron install 5", "/cron remove"],
            handler=_cmd_cron,
        )
    )
    registry.register(
        Command(
            name="update",
            summary="check for a new version; 'yes' applies it",
            usage="/update [yes]",
            long_help=(
                "With no argument, fetches origin and compares CHANGELOG.md versions, printing\n"
                "the pending changelist and stopping there -- nothing is applied. '/update yes'\n"
                "re-checks, refuses if the working tree has uncommitted changes or the local\n"
                "branch has commits origin lacks (update never stashes or merges), then runs the\n"
                "same update.sh `introspect update` runs, streaming its output into the log.\n"
                "If the web server was running, it is stopped and restarted so a changed UI\n"
                "serves fresh assets. If server/ code changed, prints a hint to run /restart --\n"
                "this process's already-imported modules stay stale until then.\n"
                "Caveat: requires a git checkout (no .git found reports and does nothing)."
            ),
            examples=["/update", "/update yes"],
            handler=_cmd_update,
            background=True,
        )
    )
    registry.register(
        Command(
            name="changelog",
            summary="show the current release's changes; 'all' for the full history",
            usage="/changelog [all]",
            long_help=(
                "With no argument, prints the newest CHANGELOG.md entry -- what the version\n"
                "you are running changed -- plus a count of older entries. '/changelog all'\n"
                "prints the entire release history, newest first.\n"
                "Caveat: if no readable CHANGELOG.md is found (or it is malformed), this\n"
                "reports that and shows nothing -- same degradation as the version banner."
            ),
            examples=["/changelog", "/changelog all"],
            handler=_cmd_changelog,
        )
    )
    registry.register(
        Command(
            name="skill",
            summary="install/update this repo's Claude skills into ~/.claude/skills",
            usage="/skill [install | status]",
            long_help=(
                "The repo ships agent skills (skills/<name>/SKILL.md) that teach any Claude\n"
                "session -- in ANY project -- how to use the archive (e.g. recalling-past-\n"
                "sessions). With no argument, reports each skill's install state (missing,\n"
                "stale, or current). 'install' renders each template for THIS checkout's\n"
                "location and writes it into ~/.claude/skills/; idempotent, and safe to re-run\n"
                "after /update to pick up skill changes.\n"
                "Caveat: writes OUTSIDE the archive (your ~/.claude directory) -- that is its\n"
                "whole purpose; nothing else in this tool touches your Claude config."
            ),
            examples=["/skill", "/skill install"],
            handler=_cmd_skill,
        )
    )
    registry.register(
        Command(
            name="exclude",
            summary="wall a project off from capture (prevention for sensitive work)",
            usage="/exclude [add <path-or-slug> [reason...] | remove <slug> | list]",
            long_help=(
                "Prevention for sensitive/classified work (spec 2026-08-17): an excluded\n"
                "project's directory is skipped by every import BEFORE anything under it is\n"
                "read -- not even filenames. Exclude BEFORE the sensitive work starts (cron\n"
                "imports run every few minutes). 'add' takes a filesystem path (encoded to\n"
                "the CLI's slug automatically) or a raw slug, plus an optional reason kept\n"
                "with the entry. Bare /exclude (or 'list') shows the wall; 'remove' takes a\n"
                "project off it.\n"
                "CAVEAT: exclusion prevents FUTURE capture only -- sessions already in the\n"
                "archive stay until deleted. Owner-only: no API path can exclude or reveal\n"
                "exclusions."
            ),
            examples=[
                "/exclude",
                "/exclude add ~/work/classified-thing client contract forbids retention",
                "/exclude add -Users-x-work-classified-thing",
                "/exclude remove -Users-x-work-classified-thing",
            ],
            handler=_cmd_exclude,
        )
    )
    registry.register(
        Command(
            name="delete",
            summary="permanently delete a session or project (ceremonied, ledgered)",
            usage="/delete <uuid-or-slug> [yes [reason...] | backups yes | forbid yes]",
            long_help=(
                "IRREVERSIBLE. Bare /delete <target> previews (counts, locations, the\n"
                "warning) and deletes NOTHING; only the explicit 'yes' form deletes. Every\n"
                "deletion writes a ledger row -- target, time, your optional reason -- so\n"
                "the archive remembers THAT it forgot, never what. After deleting, two\n"
                "SEPARATE asks are offered, never automatic: 'backups yes' scrubs backup DB\n"
                "copies (they are untouched otherwise), and 'forbid yes' adds a session to\n"
                "the re-import wall (source files still on disk would otherwise be\n"
                "re-captured by the next import). Owner-only: no API path can delete.\n"
                "For sensitive projects, prefer /exclude BEFORE the work ever starts --\n"
                "prevention is insurance, deletion is repair."
            ),
            examples=[
                "/delete 11111111-1111-1111-1111-111111111111",
                "/delete 11111111-1111-1111-1111-111111111111 yes client work, contract ended",
                "/delete 11111111-1111-1111-1111-111111111111 forbid yes",
                "/delete -Users-x-work-classified backups yes",
            ],
            handler=_cmd_delete,
            background=True,
        )
    )
    registry.register(
        Command(
            name="restart",
            summary="relaunch the TUI (picks up updated code)",
            usage="/restart",
            long_help=(
                "Exits this process and re-execs a fresh one with the same arguments -- the way\n"
                "to pick up code that /update just applied. A same-process reload would keep\n"
                "every already-imported module (the OLD code); this starts over with fresh\n"
                "imports. Any web server this TUI started is stopped first, same as /quit."
            ),
            examples=["/restart"],
            handler=_cmd_restart,
        )
    )
    registry.register(
        Command(
            name="quit",
            summary="exit the TUI (stops any web server it started)",
            usage="/quit",
            long_help=(
                "Exits the app. Any web server the TUI started is stopped on the way out.\n"
                "Ctrl-C does the same."
            ),
            examples=["/quit"],
            handler=_cmd_quit,
        )
    )
    return registry
