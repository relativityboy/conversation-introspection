"""CLI tests (Task 12): the ``introspect import|reparse|export|status`` entry point.

The four tests in the first section are the binding contract (verbatim from task-12-brief),
except ``test_cli_import_with_errors_exit_1`` which is ADAPTED the same way
``test_run.test_bad_file_does_not_halt_run`` was in Task 11 -- see that test's docstring.
The brief's premise (a directory named ``<uuid>.jsonl`` makes capture fail) does not reach
``capture_file`` at all: ``discovery._discover_project`` checks ``entry.is_dir()`` before the
``.jsonl`` name match, routes the directory through ``_discover_subagents``, and that helper
immediately bails because the *directory's* name (``<uuid>.jsonl``, with the suffix) is not
itself a bare UUID. The directory is silently skipped, never discovered, and never ingested --
so the brief's own trigger cannot produce a ``status=errors`` run. We keep the directory (so
the test still documents the brief's original intent and self-corrects if discovery's routing
ever changes) but manufacture the actual failure the same way Task 11 did: monkeypatch
``capture_file`` to raise on the ``.bak`` file, which reliably produces a file-level
``file_ingest_failure`` anomaly and ``status=errors``.

The remainder of the file covers the amendments layered onto the brief during this task:
reparse's shared advisory lock, the DB-open-failure exit code (2), and reparse/status
generally producing sane output.
"""

from __future__ import annotations

from introspect.cli import main
from introspect.ingest import run as run_module
from introspect.ingest.discovery import discover

# --- Binding contract (adapted where noted above) -----------------------------------------


def test_cli_import_and_status(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["import", "--db", dbp, "--source-root", str(fixture_tree)]) == 0
    out = capsys.readouterr().out
    assert "status=ok" in out
    assert main(["status", "--db", dbp]) == 0
    assert "sessions=" in capsys.readouterr().out


def test_cli_export_roundtrip(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    f = next(x for x in discover(fixture_tree) if x.kind == "main")
    out = tmp_path / "out.jsonl"
    assert main(["export", f.session_uuid, "-o", str(out), "--db", dbp]) == 0
    assert out.read_bytes() == f.path.read_bytes()


def test_cli_export_unknown_session_exit_1(tmp_path, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["export", "not-a-uuid", "--db", dbp]) == 1


def test_cli_import_with_errors_exit_1(tmp_path, fixture_tree, monkeypatch):
    (fixture_tree / "-Users-x-proj2" / "bbbbbbbb-1111-2222-3333-444444444444.jsonl").mkdir(
        parents=True
    )

    real_capture = run_module.capture_file

    def flaky(db, f):
        if f.kind == "backup":
            raise OSError("synthetic ingest failure")
        return real_capture(db, f)

    monkeypatch.setattr(run_module, "capture_file", flaky)
    assert main(["import", "--db", str(tmp_path / "a.db"), "--source-root", str(fixture_tree)]) == 1


# --- Amendments: reparse's shared lock, DB-open failures, general shape -------------------


def test_cli_reparse_summary_and_exit_0(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    capsys.readouterr()
    assert main(["reparse", "--db", dbp]) == 0
    out = capsys.readouterr().out
    assert "records_reparsed=" in out


def test_cli_reparse_locked_exits_1(tmp_path, fixture_tree, capsys):
    import fcntl

    dbp = tmp_path / "a.db"
    main(["import", "--db", str(dbp), "--source-root", str(fixture_tree)])
    lock = dbp.parent / "import.lock"
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["reparse", "--db", str(dbp)]) == 1
        err = capsys.readouterr().err
        assert err  # some message went to stderr


def test_cli_reparse_internal_failure_exits_1(tmp_path, fixture_tree, monkeypatch, capsys):
    """An internal reparse failure is a clean single-line stderr message + exit 1 --
    never a raw traceback with an incidental exit code."""
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    capsys.readouterr()

    def boom(db):
        raise RuntimeError("synthetic reparse failure")

    monkeypatch.setattr("introspect.cli.reparse_all", boom)
    assert main(["reparse", "--db", dbp]) == 1
    err = capsys.readouterr().err
    assert "reparse: synthetic reparse failure" in err
    # one clean message line, never a traceback (alembic migration INFO lines are permitted)
    assert "Traceback" not in err
    non_alembic = [ln for ln in err.strip().splitlines() if not ln.startswith("INFO ")]
    assert non_alembic == ["reparse: synthetic reparse failure"]


def test_cli_status_on_fresh_db(tmp_path, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["status", "--db", dbp]) == 0
    out = capsys.readouterr().out
    assert "sessions=0" in out
    assert "last run: none" in out


def test_cli_db_open_failure_exits_2(tmp_path, capsys):
    # A directory where the sqlite file should be: the DB can never be opened.
    bad_db = tmp_path / "not_a_file.db"
    bad_db.mkdir()
    assert main(["status", "--db", str(bad_db)]) == 2
    err = capsys.readouterr().err
    assert err


def test_cli_import_db_open_failure_exits_2(tmp_path, fixture_tree, capsys):
    """DB-open failure is exit 2 for EVERY subcommand, import included (coordinator fix).

    A run that never opened the DB did not 'complete with errors' -- cron distinguishes
    exit 1 (ran, recorded errors) from exit 2 (could not even open/migrate the DB).
    """
    bad_db = tmp_path / "not_a_file.db"
    bad_db.mkdir()  # a directory where the sqlite file should be
    assert main(["import", "--db", str(bad_db), "--source-root", str(fixture_tree)]) == 2
    assert capsys.readouterr().err


def test_cli_db_parent_blocked_by_file_exits_2(tmp_path, fixture_tree, capsys):
    """A FILE blocking the DB's parent directory (the default-deployment failure shape,
    e.g. a stray ~/.conversation-introspection file) is a DB-open failure -> exit 2,
    for import and the other subcommands alike."""
    (tmp_path / "blocker").write_text("not a directory")
    bad_db = str(tmp_path / "blocker" / "a.db")
    assert main(["import", "--db", bad_db, "--source-root", str(fixture_tree)]) == 2
    assert capsys.readouterr().err
    assert main(["status", "--db", bad_db]) == 2
    assert capsys.readouterr().err


def test_cli_import_mid_run_fatal_exits_1(tmp_path, fixture_tree, monkeypatch, capsys):
    """A mid-run fatal (DB opened fine, run raised later; ImportRun row finalized 'fatal'
    inside run_import) stays in the exit-1 completed-with-errors family."""

    def boom(db, discovered):
        raise RuntimeError("synthetic fatal failure")

    monkeypatch.setattr(run_module, "detect_gone", boom)
    dbp = str(tmp_path / "a.db")
    assert main(["import", "--db", dbp, "--source-root", str(fixture_tree)]) == 1
    assert capsys.readouterr().err
