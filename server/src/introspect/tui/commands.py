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

from introspect.export import (
    SessionNotFoundError,
    TranscriptNotFoundError,
    export_session_to,
)
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import DbOpenError, _acquire_lock, _release_lock, run_import
from introspect.models import ArchivedSession
from introspect.status import collect_status, counts_line, last_run_line
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
    thread-safe ``emit`` (marshalled to the UI thread). ``exit`` ends the app (``/quit``).
    """

    db_path: Path
    source_root: Path
    session_factory: Callable[[], Session]
    web: WebServerManager
    emit: Callable[[str], None]
    exit: Callable[[], None]
    registry: "CommandRegistry"


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
    ctx.emit("  Up/Down navigate results; Enter opens the session in your browser;")
    ctx.emit("  Right opens the best-matching message. Either auto-starts the web")
    ctx.emit("  server on 127.0.0.1 if it is stopped (a browser launch needs a server).")
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
    with ctx.session_factory() as db:
        snap = collect_status(db)
    ctx.emit(counts_line(snap))
    ctx.emit(last_run_line(snap))
    ctx.emit(ctx.web.describe())


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


def _cmd_start_web(ctx: CommandContext, args: list[str]) -> None:
    public = len(args) == 1 and args[0].lower() == "public"
    if args and not public:
        ctx.emit("usage: /start-web [public]")
        return
    if public:
        # Mandatory warning BEFORE the bind attempt (§16) -- the user sees the risk regardless
        # of whether the start then succeeds.
        ctx.emit(PUBLIC_BIND_WARNING)
    host = PUBLIC_HOST if public else LOCAL_HOST
    result = ctx.web.start(host)
    if result is StartResult.ALREADY_RUNNING:
        ctx.emit(f"start-web: already running at {ctx.web.local_url()}")
    elif result is StartResult.PORT_IN_USE:
        ctx.emit(
            f"start-web: cannot start -- port {ctx.web.port} is already in use by another "
            f"process. Stop it, or free the port, and try again."
        )
    elif result is StartResult.FAILED:
        ctx.emit(f"start-web: failed to bind {host}:{ctx.web.port}")
    else:
        ctx.emit(f"start-web: serving at {ctx.web.local_url()} (bound {host}:{ctx.web.port})")


def _cmd_stop_web(ctx: CommandContext, args: list[str]) -> None:
    if ctx.web.stop():
        ctx.emit("stop-web: web server stopped")
    else:
        ctx.emit("stop-web: no web server was running")


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
            examples=["/help", "/help export", "/help start-web"],
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
            name="start-web",
            summary="start the in-process web server (add 'public' to expose it)",
            usage="/start-web [public]",
            long_help=(
                "Starts the archive web server in-process on 127.0.0.1:8765. Add 'public' to\n"
                "bind 0.0.0.0 instead.\n"
                "CAVEAT (public): the archive has NO authentication -- a public bind makes every\n"
                "captured message readable by anyone on your network. A mandatory warning prints\n"
                "before the bind. If the port is already held by another process, this refuses\n"
                "cleanly (no crash). The server also auto-starts on 127.0.0.1 when you open a\n"
                "search result in the browser."
            ),
            examples=["/start-web", "/start-web public"],
            handler=_cmd_start_web,
        )
    )
    registry.register(
        Command(
            name="stop-web",
            summary="stop the in-process web server",
            usage="/stop-web",
            long_help=(
                "Stops the web server the TUI started, if any. Exiting the TUI stops it too."
            ),
            examples=["/stop-web"],
            handler=_cmd_stop_web,
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
