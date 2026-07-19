"""Command-line entry point: ``introspect import|reparse|export|status|serve``.

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
import sys
from pathlib import Path

import uvicorn

from introspect import config
from introspect.api import create_app
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.export import SessionNotFoundError, TranscriptNotFoundError, export_session_to
from introspect.ingest.reparse import reparse_all

# NOTE(claude): `_acquire_lock`/`_release_lock` are private to run.py, but the amendment for
# this task requires reparse to take the SAME advisory lock as run_import (so a concurrent
# import/reparse pair can never race). Reusing them here is a smaller diff than lifting them
# to a shared module.
from introspect.ingest.run import DbOpenError, _acquire_lock, _release_lock, run_import
from introspect.models import ChatSession, ImportRun, ParseAnomaly, RawRecord, SourceFile


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

    p_export = subparsers.add_parser("export", help="reconstruct a transcript's bytes")
    p_export.add_argument("session_uuid")
    p_export.add_argument("-o", "--output", help="output path (default: <uuid>.jsonl in cwd)")
    p_export.add_argument("--db", help="path to the archive DB")
    p_export.set_defaults(handler=_cmd_export)

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


def _cmd_status(args: argparse.Namespace) -> int:
    dbp = _resolve_db_path_or_none(args.db)
    if dbp is None:
        return 2
    engine = _open_db_or_none(dbp)
    if engine is None:
        return 2
    with session_factory(engine)() as db:
        sessions = db.query(ChatSession).count()
        files = db.query(SourceFile).count()
        records = db.query(RawRecord).count()
        anomalies = db.query(ParseAnomaly).count()
        errors = db.query(ParseAnomaly).filter_by(severity="error").count()
        warns = db.query(ParseAnomaly).filter_by(severity="warn").count()
        infos = db.query(ParseAnomaly).filter_by(severity="info").count()
        last_run = db.query(ImportRun).order_by(ImportRun.id.desc()).first()

    print(
        f"sessions={sessions} files={files} records={records} "
        f"anomalies={anomalies} (error={errors} warn={warns} info={infos})"
    )
    if last_run is None:
        print("last run: none")
    else:
        print(
            f"last run: id={last_run.id} trigger={last_run.trigger} "
            f"status={last_run.status} finished_at={last_run.finished_at}"
        )
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
