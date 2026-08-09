"""Command-line entry point: ``introspect import|reparse|export|unarchive|status|serve|tui``.

Thin layer — every subcommand resolves config, opens/migrates the DB, and delegates to the
module that owns the logic (:mod:`introspect.ingest.run`, :mod:`introspect.ingest.reparse`,
:mod:`introspect.export`). No business logic lives here.

Exit codes (spec §7), uniform across all four subcommands:

    0  success — including ``import``'s ``already_running`` (a no-op, not a failure)
    1  ran but with errors — an import run recorded error-severity anomalies or failed
       mid-run (its ImportRun row is finalized ``'fatal'`` inside :func:`run_import`),
       reparse found the advisory lock already held, export named a session/transcript
       that doesn't exist, or reparse/export failed internally after the DB opened
    2  could not open/migrate the DB — nothing ran, for ANY subcommand

``import`` delegates its DB lifecycle (lock, open, migrate) to :func:`run_import`, which
raises :class:`DbOpenError` when the engine-open/migrate step fails so this module can map it
to exit 2 without duplicating the DB-opening; any other exception from ``run_import`` is a
mid-run fatal → exit 1. ``reparse``, ``export``, ``status``, and ``serve`` open+migrate the
DB directly here via ``_open_db_or_none``, whose failure likewise maps to exit 2.

Summary lines go to stdout; error/detail messages go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from introspect import config, cron, update
from introspect.api import create_app
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.export import SessionNotFoundError, TranscriptNotFoundError, export_session_to
from introspect.ingest.recapture import recapture_file
from introspect.ingest.reparse import reparse_all

# NOTE(claude): `_acquire_lock`/`_release_lock` are private to run.py, but the amendment for
# this task requires reparse to take the SAME advisory lock as run_import (so a concurrent
# import/reparse pair can never race). Reusing them here is a smaller diff than lifting them
# to a shared module.
from introspect.ingest.run import DbOpenError, _acquire_lock, _release_lock, run_import
from introspect.models import ArchivedSession
from introspect.status import collect_status, counts_line, last_run_line, schema_line


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="introspect")
    subparsers = parser.add_subparsers(required=True)

    p_import = subparsers.add_parser("import", help="ingest new/changed transcripts")
    p_import.add_argument("--db", help="path to the archive DB")
    p_import.add_argument("--source-root", help="root directory of transcripts to scan")
    p_import.set_defaults(handler=_cmd_import)

    p_reparse = subparsers.add_parser("reparse", help="rebuild interpretation from raw bytes")
    p_reparse.add_argument("--db", help="path to the archive DB")
    p_reparse.set_defaults(handler=_cmd_reparse)

    p_recapture = subparsers.add_parser(
        "recapture", help="byte-reconciled repair of a shattered source file's raw records"
    )
    p_recapture.add_argument("session_uuid")
    p_recapture.add_argument("--kind", default="main", help="transcript kind (default: main)")
    p_recapture.add_argument(
        "--agent-hex", dest="agent_hex_id", help="subagent hex id (only with --kind subagent)"
    )
    p_recapture.add_argument("--db", help="path to the archive DB")
    p_recapture.add_argument(
        "--dry-run", action="store_true", help="report the would-be swap without mutating"
    )
    p_recapture.set_defaults(handler=_cmd_recapture)

    p_export = subparsers.add_parser("export", help="reconstruct a transcript's bytes")
    p_export.add_argument("session_uuid")
    p_export.add_argument("-o", "--output", help="output path (default: <uuid>.jsonl in cwd)")
    p_export.add_argument("--db", help="path to the archive DB")
    p_export.set_defaults(handler=_cmd_export)

    p_unarchive = subparsers.add_parser(
        "unarchive", help="restore an archived session (by uuid; CLI-only)"
    )
    p_unarchive.add_argument("session_uuid")
    p_unarchive.add_argument("--db", help="path to the archive DB")
    p_unarchive.set_defaults(handler=_cmd_unarchive)

    p_status = subparsers.add_parser("status", help="archive counts + last import run")
    p_status.add_argument("--db", help="path to the archive DB")
    p_status.set_defaults(handler=_cmd_status)

    p_serve = subparsers.add_parser("serve", help="run the read-only archive API server")
    p_serve.add_argument("--db", help="path to the archive DB")
    p_serve.add_argument("--port", type=int, default=8765, help="port to listen on")
    p_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "host/interface to bind (default: 127.0.0.1, localhost-only -- binding to "
            "0.0.0.0 or any other non-local address exposes your private transcript "
            "archive, including message contents, to the network)"
        ),
    )
    p_serve.set_defaults(handler=_cmd_serve)

    p_tui = subparsers.add_parser("tui", help="interactive terminal UI (search + slash commands)")
    p_tui.add_argument("--db", help="path to the archive DB")
    p_tui.add_argument("--source-root", help="root directory of transcripts to scan")
    p_tui.set_defaults(handler=_cmd_tui)

    p_cron = subparsers.add_parser("cron", help="manage the scheduled-import crontab entry")
    cron_sub = p_cron.add_subparsers(required=True)
    c_status = cron_sub.add_parser("status", help="show whether the import job is scheduled")
    c_status.set_defaults(handler=_cmd_cron_status)
    c_install = cron_sub.add_parser("install", help="install or replace the scheduled import job")
    c_install.add_argument(
        "--every", type=int, default=15, metavar="MINUTES", help="run every N minutes (1-60)"
    )
    c_install.set_defaults(handler=_cmd_cron_install)
    c_remove = cron_sub.add_parser("remove", help="remove the scheduled import job")
    c_remove.set_defaults(handler=_cmd_cron_remove)

    p_update = subparsers.add_parser(
        "update", help="check for and apply updates from the repo's origin"
    )
    p_update.add_argument(
        "--yes", "-y", action="store_true", help="apply without prompting"
    )
    p_update.set_defaults(handler=_cmd_update)

    return parser


def _cmd_import(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    root = config.source_root(args.source_root)
    try:
        summary = run_import(dbp, root)
    except DbOpenError as exc:
        print(f"import: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- mid-run fatal; run's own 'fatal' row has detail
        print(f"import: {exc}", file=sys.stderr)
        return 1
    print(
        f"imported files={summary.files_seen} records={summary.records_added} "
        f"dupes={summary.records_skipped_duplicate} anomalies={summary.anomaly_count} "
        f"gone={summary.gone_flipped} status={summary.status}"
    )
    return 0 if summary.status in ("ok", "already_running") else 1


def _cmd_reparse(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    lock_fh = _acquire_lock(dbp.parent / "import.lock")
    if lock_fh is None:
        print("reparse: another import/reparse is already running", file=sys.stderr)
        return 1
    try:
        engine = _open_db_or_none(dbp)
        if engine is None:
            return 2
        with session_factory(engine)() as db:
            stats = reparse_all(db)
    except Exception as exc:  # noqa: BLE001 -- internal failure: one clean line, exit 1
        print(f"reparse: {exc}", file=sys.stderr)
        return 1
    finally:
        _release_lock(lock_fh)
    print(
        f"reparse records_reparsed={stats.records_reparsed} "
        f"anomalies_before={stats.anomalies_before} anomalies_after={stats.anomalies_after}"
    )
    return 0


def _cmd_recapture(args: argparse.Namespace) -> int:
    """Byte-reconciled repair of one transcript's shattered raw records (compat spec §3).

    Same lock/exit-code idiom as ``_cmd_reparse``: the advisory ``import.lock`` guards against
    a concurrent import/reparse racing the swap, and DB-open failure is exit 2. A REFUSED
    outcome (``reconciled=False`` -- the byte gate failed, or a unit straddled the checkpoint)
    is exit 1 with the diagnosis on stderr, matching every other "ran but failed" case; success
    (including a reconciled ``--dry-run``) is exit 0.
    """
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    lock_fh = _acquire_lock(dbp.parent / "import.lock")
    if lock_fh is None:
        print("recapture: another import/reparse is already running", file=sys.stderr)
        return 1
    try:
        engine = _open_db_or_none(dbp)
        if engine is None:
            return 2
        with session_factory(engine)() as db:
            try:
                stats = recapture_file(
                    db,
                    args.session_uuid,
                    kind=args.kind,
                    agent_hex_id=args.agent_hex_id,
                    dry_run=args.dry_run,
                )
            except (SessionNotFoundError, TranscriptNotFoundError) as exc:
                print(f"recapture: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:  # noqa: BLE001 -- internal failure: one clean line, exit 1
                print(f"recapture: {exc}", file=sys.stderr)
                return 1
    finally:
        _release_lock(lock_fh)
    if not stats.reconciled:
        print(f"recapture: refused — {stats.reason}", file=sys.stderr)
    print(
        f"recapture file={stats.path} reconciled={stats.reconciled} "
        f"records {stats.records_before}->{stats.records_after} "
        f"anomalies {stats.anomalies_before}->{stats.anomalies_after}"
    )
    return 0 if stats.reconciled else 1


def _cmd_export(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    engine = _open_db_or_none(dbp)
    if engine is None:
        return 2
    out_path = Path(args.output) if args.output else Path.cwd() / f"{args.session_uuid}.jsonl"
    with session_factory(engine)() as db:
        try:
            export_session_to(db, args.session_uuid, out_path)
        except (SessionNotFoundError, TranscriptNotFoundError) as exc:
            # expected not-found outcomes; same exit-1 family as internal failures below
            print(f"export: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 -- internal failure: one clean line, exit 1
            print(f"export: {exc}", file=sys.stderr)
            return 1
    return 0


def _cmd_unarchive(args: argparse.Namespace) -> int:
    """Restore an archived session by uuid (§15.1: the ONLY unarchive path -- no API/UI reveals
    or removes archived sessions). A uuid that isn't currently archived (unknown session, or a
    known-but-not-archived one) is exit 1 with a stderr message; there is no list command, so the
    user supplies the uuid out-of-band via a direct DB query."""
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    engine = _open_db_or_none(dbp)
    if engine is None:
        return 2
    with session_factory(engine)() as db:
        row = db.get(ArchivedSession, args.session_uuid)
        if row is None:
            print(f"unarchive: no archived session {args.session_uuid}", file=sys.stderr)
            return 1
        db.delete(row)
        db.commit()
    print(f"unarchived {args.session_uuid}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    engine = _open_db_or_none(dbp)
    if engine is None:
        return 2
    with session_factory(engine)() as db:
        snap = collect_status(db)
    # `archived` is an aggregate count only (§15.1: "n only, no identities") -- status never
    # enumerates which sessions are archived. Format lives in `introspect.status` so the CLI and
    # the TUI `/status` report identical numbers from one source of truth.
    print(counts_line(snap))
    print(last_run_line(snap))
    print(schema_line(snap))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    # NOTE(claude): validate DB open/migrate here (same exit-2 mapping as the other
    # subcommands) even though create_app() below independently opens+migrates again --
    # that duplication is intentional so create_app stays self-sufficient for callers
    # (tests, future embedders) that construct it directly without going through this CLI.
    if _open_db_or_none(dbp) is None:
        return 2
    app = create_app(db_path=dbp)
    if app.state.ui_dist is not None:
        print("UI: serving web/dist")
    else:
        print("UI: not built (API only) — cd web && npm run build")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _cmd_tui(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    # Validate DB open/migrate up front (same exit-2 mapping as the other subcommands) before
    # entering the full-screen app, so a broken DB fails with a clean stderr line instead of a
    # traceback painted over the alt-screen.
    if _open_db_or_none(dbp) is None:
        return 2
    # Lazy import: the TUI pulls in `textual` (and rich), which cron's `introspect import` must
    # never pay for. Keeping this import inside the handler is the seam that guarantees it.
    from introspect.tui.app import run_tui

    result = run_tui(db_path=dbp, source_root=config.source_root(args.source_root))
    if result == "restart":
        # A same-process relaunch would keep every already-imported module -- old code. app.run()
        # has returned (terminal restored), so exec a fresh process here, never from inside
        # Textual, so the updated server code from /update actually loads.
        os.execvp(sys.argv[0], sys.argv)
    return 0


# --- cron: manage the single scheduled-import crontab line (no DB involved) ----------------
# These verbs never open the archive DB -- they only read/rewrite the user crontab -- so they
# do not share the exit-2 DB-open family. A cron failure (bad interval, unusable binary, crontab
# I/O) is one clean stderr line + exit 1; success is one stdout line + exit 0.


def _cmd_cron_status(args: argparse.Namespace) -> int:
    notice = cron.macos_permission_notice()
    if notice is not None:
        print(notice)
    try:
        st = cron.status(cron.CrontabIO())
    except cron.CronError as exc:
        print(f"cron: {exc}", file=sys.stderr)
        return 1
    print(cron.status_line(st))
    if st.line is not None:
        print(st.line)
    return 0


def _cmd_cron_install(args: argparse.Namespace) -> int:
    notice = cron.macos_permission_notice()
    if notice is not None:
        print(notice)
    try:
        st = cron.install(cron.CrontabIO(), minutes=args.every)
    except cron.CronError as exc:
        print(f"cron: {exc}", file=sys.stderr)
        return 1
    print(f"cron: installed ({cron.status_line(st).removeprefix('cron: ')})")
    print(st.line)
    return 0


def _cmd_cron_remove(args: argparse.Namespace) -> int:
    notice = cron.macos_permission_notice()
    if notice is not None:
        print(notice)
    try:
        removed = cron.remove(cron.CrontabIO())
    except cron.CronError as exc:
        print(f"cron: {exc}", file=sys.stderr)
        return 1
    print("cron: removed" if removed else "cron: nothing to remove (not installed)")
    return 0


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
        print(
            "(this check compares versions only -- after a manual git pull, run "
            "./update.sh to rebuild)"
        )
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
        try:
            reply = input(f"update to {chk.remote_version}? [y/N] ").strip().lower()
        except EOFError:
            # Closed stdin (e.g. a non-interactive/scripted invocation without --yes) -- treat
            # like a decline rather than letting the bare EOFError traceback out.
            print("not updating.")
            return 0
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


def _resolve_db_path_or_none(cli_value: str | None) -> Path | None:
    """Resolve the DB path (creating its parent dir), or stderr + None on failure.

    ``config.db_path`` mkdirs the parent; a FILE blocking that path (the default-deployment
    failure shape, e.g. a stray ``~/.conversation-introspection`` file) is a DB-open failure
    and belongs to the exit-2 family for every subcommand.
    """
    try:
        return config.db_path(cli_value)
    except OSError as exc:
        print(f"could not open database: {exc}", file=sys.stderr)
        return None


def _open_db_or_none(dbp: Path):  # noqa: ANN202 -- returns an Engine or None
    """Open + migrate the archive DB, or print a stderr message and return None on failure.

    Isolates the one thing that maps to exit 2: the DB could not even be opened/migrated.
    """
    try:
        engine = get_engine(dbp)
        upgrade_to_head(engine)
    except Exception as exc:  # noqa: BLE001 -- any open/migrate failure is fatal (exit 2)
        print(f"could not open database {dbp}: {exc}", file=sys.stderr)
        return None
    return engine
