"""Orchestrator: the cron-safe ``run_import`` entry point.

One import run composes the whole ingest pipeline behind a single advisory lock so two
overlapping cron ticks can never interleave writes to the same archive:

1. **Lock FIRST.** An exclusive, non-blocking ``fcntl.flock`` on ``<db_dir>/import.lock`` is
   taken before anything else. If it is already held, we return immediately with status
   ``already_running`` -- no engine is opened, no migration is run, and no ``ImportRun`` row is
   written. (fcntl locks are per open-file-description, so a concurrent holder in any process,
   including a separate fd in this one, contends correctly.)
2. **Then migrate.** Only under the lock do we open the engine and ``upgrade_to_head`` -- the DB
   self-migrates so a cron entry needs no separate deploy step, and two runs can never migrate
   at once.
3. **Capture, contained per file.** Each discovered file is captured inside its own
   ``try/except``; a failure becomes a file-level ``file_ingest_failure`` error anomaly and the
   run continues with the remaining files, so one unreadable transcript can never abort a whole
   import.
4. **detect_gone**, then the **unparsed-row sweep** -- the self-healing pass that re-interprets
   any ``raw_records`` left with a NULL provenance stamp (a crash between capture's sacred
   commit and its separate interpretation commit, or a rolled-back chunk; see
   ``capture._interpret_chunk``).

``status`` is ``errors`` iff this run created any error-severity anomaly, else ``ok``. An
unhandled exception finalizes the row as ``fatal`` (finished_at + best-effort counts) and
re-raises, so cron fails loudly and no zombie ``running`` row is left behind. Known residual
(spec-owner decision): an API-precreated row whose DB is unopenable by both the worker
(:class:`DbOpenError`) and the API's best-effort finalize attempt stays ``'running'`` -- an
accepted residual alongside SIGKILL. No reaper by decision: archive imports are seconds-long;
a reaper is machinery for a case that requires the DB to be down anyway.

The ``run_id`` seam (Task P2-8): the ``POST /import`` API handler pre-creates the ``ImportRun``
row (``trigger='api'``, ``status='running'``) under its own probe of the lock and passes its id
to :func:`run_import`, which then FINALIZES that existing row instead of inserting one. The
"already_running fast path writes no row" rule above holds for every CLI/cron caller
(``run_id is None``); on the API path a lock lost between the handler's probe and this call
instead finalizes the pre-created row ``'errors'`` (never stranded ``'running'``) via
:func:`_finalize_lost_race`. :class:`DbOpenError` is the one exit that cannot finalize the
pre-created row from here (the DB never opened); the API's thread wrapper makes its own
best-effort ``'fatal'`` finalize, with the both-fail case the accepted residual above.

Known limitation: a capture failure mid-file under-counts that run's ``records_added`` (lines
committed before the failure are archived but uncounted). DB row counts are the truth;
per-run counters are best-effort observability.
"""

from __future__ import annotations

import fcntl
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest import interpret
from introspect.ingest.capture import capture_file, detect_gone, utcnow
from introspect.ingest.discovery import DiscoveredFile, discover
from introspect.models import ImportRun, ParseAnomaly, RawRecord, SourceFile
from introspect.schema import SCHEMA_VERSION, parse_line
from introspect.schema_versions import ensure_current_schema_version_recorded


class DbOpenError(RuntimeError):
    """The archive DB could not be opened or migrated.

    Raised by :func:`run_import` when the engine-open/``upgrade_to_head`` step fails — i.e.
    before any import work (or ``ImportRun`` row) exists. The CLI maps this to exit 2
    (fatal DB-open, uniform across all subcommands per spec §7); any exception raised
    *after* the DB opened is a mid-run fatal (row finalized ``'fatal'``) and maps to exit 1.
    """


@dataclass
class ImportSummary:
    run_id: int
    files_seen: int
    records_added: int
    records_skipped_duplicate: int
    anomaly_count: int
    gone_flipped: int
    status: str  # 'ok' | 'errors' | 'already_running'
    records_swept: int = 0  # extra: raw records re-interpreted by the self-healing sweep


_ALREADY_RUNNING = ImportSummary(
    run_id=0,
    files_seen=0,
    records_added=0,
    records_skipped_duplicate=0,
    anomaly_count=0,
    gone_flipped=0,
    status="already_running",
    records_swept=0,
)


def run_import(
    db_path: Path, root: Path, trigger: str = "cli", run_id: int | None = None
) -> ImportSummary:
    """Take the exclusive advisory lock, migrate, and run one full import. Cron-safe.

    See the module docstring for the ordered contract. Returns an :class:`ImportSummary`; a
    contended lock returns the ``already_running`` summary without opening the engine, running
    a migration, or writing an ``ImportRun`` row. Raises :class:`DbOpenError` if the DB cannot
    be opened/migrated (nothing ran); any other exception is a mid-run fatal (row finalized
    ``'fatal'``, see :func:`_finalize_fatal`).

    ``run_id`` is the API seam (Task P2-8). When ``None`` (every CLI/cron caller) the
    ``ImportRun`` row is created here as before. When set, the ``POST /import`` handler already
    created and committed that row under its own lock probe, so this call FINALIZES it instead
    of inserting one -- and if the lock was lost between the handler's probe and here (a
    concurrent run grabbed it), the pre-created row is finalized ``'errors'`` with zeroed counts
    rather than left stranded ``'running'`` (see :func:`_finalize_lost_race`).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = _acquire_lock(db_path.parent / "import.lock")
    if lock_fh is None:
        if run_id is not None:
            _finalize_lost_race(db_path, run_id)
        return _ALREADY_RUNNING
    try:
        try:
            engine = get_engine(db_path)
            upgrade_to_head(engine)
        except Exception as exc:
            raise DbOpenError(f"could not open database {db_path}: {exc}") from exc
        with session_factory(engine)() as db:
            return _run_locked(db, root, trigger, run_id)
    finally:
        _release_lock(lock_fh)


def _finalize_lost_race(db_path: Path, run_id: int) -> None:
    """Finalize an API pre-created ``ImportRun`` row when the lock was lost after the probe.

    The ``POST /import`` handler already committed this row as ``'running'``; we could not take
    the lock (a concurrent run holds it), so no import work runs, but the row must not be left
    stranded. Mark it ``'errors'`` with ``finished_at`` set and counts zeroed so the polling
    client sees an honest terminal state. The DB is already migrated (the API opened it), so we
    open the engine WITHOUT re-running migrations -- and this whole finalize is best-effort: a
    failure here must never crash the worker thread.
    """
    try:
        engine = get_engine(db_path)
        with session_factory(engine)() as db:
            run = db.get(ImportRun, run_id)
            if run is None:  # the pre-created row never became durable -- nothing to finalize
                return
            run.status = "errors"
            run.finished_at = utcnow()
            run.files_seen = 0
            run.records_added = 0
            run.records_skipped_duplicate = 0
            run.anomaly_count = 0
            db.commit()
    except Exception:  # noqa: BLE001, S110 -- best-effort; must not crash the worker
        pass


def _acquire_lock(lock_path: Path):  # noqa: ANN202 -- returns an open file handle or None
    """Non-blocking exclusive ``flock``. Returns the held handle, or None if already locked."""
    fh = lock_path.open("a")  # 'a': create-if-missing, never truncate; contents are unused
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # BlockingIOError (EAGAIN/EWOULDBLOCK): another run holds it
        fh.close()
        return None
    return fh


def _release_lock(lock_fh) -> None:  # noqa: ANN001 -- the handle _acquire_lock returned
    """Unlock and close a handle previously returned by :func:`_acquire_lock`."""
    fcntl.flock(lock_fh, fcntl.LOCK_UN)
    lock_fh.close()


def _run_locked(db, root: Path, trigger: str, run_id: int | None = None) -> ImportSummary:
    # Anomalies created during THIS run are those with an id past the pre-run high-water mark
    # (autoincrement is monotonic and we are the only writer under the lock). This is exact and
    # free of any timestamp-format assumptions.
    baseline_anomaly_id = db.query(func.max(ParseAnomaly.id)).scalar() or 0

    # Provenance: record the running SCHEMA_VERSION the first time this codebase imports against
    # this archive (idempotent no-op thereafter). Done under the lock, before any capture work.
    ensure_current_schema_version_recorded(db)

    if run_id is None:
        run = ImportRun(trigger=trigger, started_at=utcnow(), status="running")
        db.add(run)
        db.commit()
    else:
        # API seam: the POST /import handler pre-created and committed this row
        # (trigger='api', status='running'); finalize IT rather than inserting a second row.
        # Its started_at is the handler's request time and is left untouched. The fatal path
        # below (_finalize_fatal, keyed on run.id) then also finalizes this same pre-created row.
        run = db.get(ImportRun, run_id)
        if run is None:
            # NOTE(claude): mirrors _finalize_lost_race's guard -- the pre-created row vanished
            # (unreachable in practice), so fall through to creating a fresh one rather than
            # crashing on a None attribute access below.
            run = ImportRun(trigger=trigger, started_at=utcnow(), status="running")
            db.add(run)
            db.commit()

    files_seen = 0
    records_added = 0
    records_skipped_duplicate = 0
    try:
        discovered = list(discover(root))
        files_seen = len(discovered)
        for f in discovered:
            try:
                stats = capture_file(db, f)
            except Exception as exc:  # noqa: BLE001 -- one bad file must never abort the run
                db.rollback()  # discard any partial post-commit state from the failed capture
                _record_file_ingest_failure(db, f, exc)
                continue
            records_added += stats.records_added
            records_skipped_duplicate += stats.records_skipped_duplicate

        gone_flipped = detect_gone(db, discovered)
        records_swept = _sweep_unparsed(db)
        # Authorship post-pass (spec 2026-08-07 §4): classifies every message row this run
        # (or an earlier crashed run) left with a NULL authorship_kind. Idempotent and driven
        # purely by that NULL, so it costs nothing on a run that added no new messages.
        interpret.classify_pending(db)

        this_run_anomalies = db.query(ParseAnomaly).filter(
            ParseAnomaly.id > baseline_anomaly_id
        )
        anomaly_count = this_run_anomalies.count()
        has_errors = (
            this_run_anomalies.filter(ParseAnomaly.severity == "error").count() > 0
        )
        status = "errors" if has_errors else "ok"

        run.finished_at = utcnow()
        run.files_seen = files_seen
        run.records_added = records_added
        run.records_skipped_duplicate = records_skipped_duplicate
        run.anomaly_count = anomaly_count
        run.status = status
        db.commit()
    except Exception:
        # Fail loudly for cron, but never leave a zombie 'running' row: finalize as 'fatal'
        # with best-effort counts, then RE-RAISE the original exception.
        _finalize_fatal(
            db, run.id, files_seen, records_added, records_skipped_duplicate,
            baseline_anomaly_id,
        )
        raise

    return ImportSummary(
        run_id=run.id,
        files_seen=files_seen,
        records_added=records_added,
        records_skipped_duplicate=records_skipped_duplicate,
        anomaly_count=anomaly_count,
        gone_flipped=gone_flipped,
        status=status,
        records_swept=records_swept,
    )


def _finalize_fatal(
    db,
    run_id: int,
    files_seen: int,
    records_added: int,
    records_skipped_duplicate: int,
    baseline_anomaly_id: int,
) -> None:
    """Best-effort 'fatal' finalize of the ImportRun row. Never raises.

    The finalize itself may be what is failing (a dead DB), so everything here is guarded:
    a failure to finalize must not mask the original exception the caller is re-raising.
    ``db.rollback()`` first — the session is in an undefined mid-failure state.
    """
    try:
        db.rollback()
        run = db.get(ImportRun, run_id)
        if run is None:  # the ImportRun insert itself never became durable
            return
        run.status = "fatal"
        run.finished_at = utcnow()
        run.files_seen = files_seen
        run.records_added = records_added
        run.records_skipped_duplicate = records_skipped_duplicate
        run.anomaly_count = (
            db.query(ParseAnomaly).filter(ParseAnomaly.id > baseline_anomaly_id).count()
        )
        db.commit()
    except Exception:  # noqa: BLE001, S110 -- must not mask the original exception
        pass


def _record_file_ingest_failure(db, f: DiscoveredFile, exc: Exception) -> None:
    """Record a contained capture failure as a file-level error anomaly and commit it.

    ``source_file_id`` is filled when the row is resolvable (capture commits the structural
    ``SourceFile`` before it reads a byte, so a mid-read failure still has one), else NULL.
    """
    source_file = (
        db.query(SourceFile)
        .filter_by(path=str(f.path))
        .order_by(SourceFile.generation.desc())
        .first()
    )
    db.add(
        ParseAnomaly(
            raw_record_id=None,
            source_file_id=source_file.id if source_file is not None else None,
            severity="error",
            kind="file_ingest_failure",
            detail={"path": str(f.path), "error": str(exc)},
            schema_version=None,
            created_at=utcnow(),
        )
    )
    db.commit()


def _sweep_unparsed(db) -> int:
    """Re-interpret every ``raw_records`` row still lacking a provenance stamp. Returns the count.

    This is the self-healing pass for records captured but never interpreted -- a crash between
    capture's sacred commit and its separate interpretation commit, or a chunk whose
    interpretation rolled back (see ``capture._interpret_chunk``). Each record is handled with
    the same containment ``_interpret_chunk`` uses: a whitespace line is graded via the shared
    ``grade_whitespace_line`` helper; any other line is re-parsed and ``apply``-ied, and an
    ``apply`` exception rolls the record back, records an ``interpret_failure`` error anomaly,
    stamps ``parse_status='anomaly'``, commits, and moves on. In a clean run this finds nothing.

    Retry policy (spec-owner decision): a NULL-stamped record that already carries an
    ``interpret_failure`` anomaly is EXCLUDED -- its failure is deterministic under the current
    schema, and retrying it every cron tick would only churn duplicate anomalies. It stays
    visible via ``parse_status='anomaly'`` + its anomaly row, and is retried when the SCHEMA
    changes: reparse deletes ``interpret_failure`` anomalies before re-running (see
    ``reparse._INTERPRETATION_ANOMALY_KINDS``), which re-arms the record for interpretation.
    Sibling-casualty rows (NULL stamp from a rolled-back chunk, no anomaly of their own) still
    sweep normally.
    """
    has_prior_failure = (
        db.query(ParseAnomaly.id)
        .filter(
            ParseAnomaly.raw_record_id == RawRecord.id,
            ParseAnomaly.kind == "interpret_failure",
        )
        .exists()
    )
    raw_ids = [
        rid
        for (rid,) in db.query(RawRecord.id)
        .filter(RawRecord.parsed_with_schema_version.is_(None), ~has_prior_failure)
        .order_by(RawRecord.source_file_id, RawRecord.line_number)
        .all()
    ]

    swept = 0
    for raw_id in raw_ids:
        raw = db.get(RawRecord, raw_id)
        if raw is None:
            continue
        swept += 1
        if interpret.is_whitespace_line(raw.raw_line):
            interpret.grade_whitespace_line(db, raw)
            db.commit()
            continue
        pr = parse_line(raw.raw_line)
        try:
            interpret.apply(db, pr, raw)
            db.commit()
        except Exception as exc:  # noqa: BLE001 -- must never abort the rest of the sweep
            db.rollback()
            raw = db.get(RawRecord, raw_id)  # re-fetch: rollback expired the instance
            db.add(
                ParseAnomaly(
                    raw_record_id=raw.id,
                    source_file_id=raw.source_file_id,
                    severity="error",
                    kind="interpret_failure",
                    detail={"error": str(exc)},
                    schema_version=SCHEMA_VERSION,
                    created_at=utcnow(),
                )
            )
            raw.parse_status = "anomaly"
            db.commit()
    return swept
