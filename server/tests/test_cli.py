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

from pathlib import Path

from introspect.cli import main
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest import run as run_module
from introspect.ingest.capture import utcnow
from introspect.ingest.discovery import discover
from introspect.models import ArchivedSession


def _open(dbp: str) -> session_factory:
    engine = get_engine(Path(dbp))
    upgrade_to_head(engine)
    return session_factory(engine)


def _archive(dbp: str, session_uuid: str) -> None:
    with _open(dbp)() as db:
        db.add(ArchivedSession(session_uuid=session_uuid, created_at=utcnow()))
        db.commit()


def _is_archived(dbp: str, session_uuid: str) -> bool:
    with _open(dbp)() as db:
        return db.get(ArchivedSession, session_uuid) is not None

# --- Binding contract (adapted where noted above) -----------------------------------------


def test_cli_import_and_status(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["import", "--db", dbp, "--source-root", str(fixture_tree)]) == 0
    out = capsys.readouterr().out
    assert "status=ok" in out
    assert main(["status", "--db", dbp]) == 0
    status_out = capsys.readouterr().out
    assert "sessions=" in status_out
    assert "schema: introspect-schema/" in status_out


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


# --- Archive: unarchive (CLI-only restore) + status archived count (§15.1) ---------------


def test_cli_unarchive_restores_session(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    f = next(x for x in discover(fixture_tree) if x.kind == "main")
    _archive(dbp, f.session_uuid)
    capsys.readouterr()

    assert main(["unarchive", f.session_uuid, "--db", dbp]) == 0
    out = capsys.readouterr().out
    assert f.session_uuid in out
    assert not _is_archived(dbp, f.session_uuid)


def test_cli_unarchive_unknown_uuid_exits_1(tmp_path, fixture_tree, capsys):
    """An unknown (or simply not-archived) uuid is exit 1 with a stderr message -- there is no
    list command that could reveal what IS archived, so the user supplies the uuid out-of-band."""
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    capsys.readouterr()

    assert main(["unarchive", "not-archived-uuid", "--db", dbp]) == 1
    assert capsys.readouterr().err


def test_cli_unarchive_db_open_failure_exits_2(tmp_path, capsys):
    bad_db = tmp_path / "not_a_file.db"
    bad_db.mkdir()
    assert main(["unarchive", "whatever", "--db", str(bad_db)]) == 2
    assert capsys.readouterr().err


def test_cli_status_shows_archived_count(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    f = next(x for x in discover(fixture_tree) if x.kind == "main")
    _archive(dbp, f.session_uuid)
    capsys.readouterr()

    assert main(["status", "--db", dbp]) == 0
    assert "archived=1" in capsys.readouterr().out


def test_cli_import_mid_run_fatal_exits_1(tmp_path, fixture_tree, monkeypatch, capsys):
    """A mid-run fatal (DB opened fine, run raised later; ImportRun row finalized 'fatal'
    inside run_import) stays in the exit-1 completed-with-errors family."""

    def boom(db, discovered):
        raise RuntimeError("synthetic fatal failure")

    monkeypatch.setattr(run_module, "detect_gone", boom)
    dbp = str(tmp_path / "a.db")
    assert main(["import", "--db", dbp, "--source-root", str(fixture_tree)]) == 1
    assert capsys.readouterr().err


# --- cron: scheduled-import crontab management --------------------------------------------
# Every cron test injects a fake `crontab` subprocess (via the module-level runner seam), so no
# test ever reads or writes the developer's real crontab. `resolve_binary`/`DEFAULT_LOG` are
# pinned to temp locations so install never touches PATH or the real home directory.


def _seed_crontab(monkeypatch, initial=""):
    from tests.test_cron import FakeRunner

    runner = FakeRunner(read_text=initial)
    monkeypatch.setattr("introspect.cron._subprocess_runner", runner)
    return runner


def test_cli_cron_status_not_installed(tmp_path, monkeypatch, capsys):
    _seed_crontab(monkeypatch, "MAILTO=me@example.com\n")
    assert main(["cron", "status"]) == 0
    assert "cron: not installed" in capsys.readouterr().out


def test_cli_cron_install_status_remove_roundtrip(tmp_path, monkeypatch, capsys):
    import introspect.cron as cron_mod
    from tests.test_cron import make_exec

    runner = _seed_crontab(monkeypatch, "MAILTO=me@example.com\n")
    binary = make_exec(tmp_path)
    monkeypatch.setattr(cron_mod, "resolve_binary", lambda **k: binary)
    monkeypatch.setattr(cron_mod, "DEFAULT_LOG", tmp_path / "cron.log")

    assert main(["cron", "install", "--every", "5"]) == 0
    assert "installed" in capsys.readouterr().out
    written = runner.written[-1]
    assert written.count(cron_mod.MARKER) == 1  # exactly one managed line
    assert "*/5 * * * *" in written
    assert written.startswith("MAILTO=me@example.com\n")  # unrelated line preserved

    assert main(["cron", "status"]) == 0
    assert "installed@5m" in capsys.readouterr().out

    assert main(["cron", "remove"]) == 0
    assert "removed" in capsys.readouterr().out
    assert runner.written[-1] == "MAILTO=me@example.com\n"  # byte-for-byte back to original


def test_cli_cron_install_rejects_unusable_binary(tmp_path, monkeypatch, capsys):
    import introspect.cron as cron_mod

    _seed_crontab(monkeypatch, "")
    monkeypatch.setattr(cron_mod, "resolve_binary", lambda **k: tmp_path / "nope")
    assert main(["cron", "install"]) == 1
    assert capsys.readouterr().err


def test_cli_cron_install_rejects_bad_interval(tmp_path, monkeypatch, capsys):
    _seed_crontab(monkeypatch, "")
    assert main(["cron", "install", "--every", "0"]) == 1
    assert capsys.readouterr().err


def test_cli_cron_remove_when_absent(tmp_path, monkeypatch, capsys):
    _seed_crontab(monkeypatch, "MAILTO=me@example.com\n")
    assert main(["cron", "remove"]) == 0
    assert "nothing to remove" in capsys.readouterr().out
