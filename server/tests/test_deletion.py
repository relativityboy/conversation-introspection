"""Reasoned deletion (spec 2026-08-17 §3): FTS-safe cascade, ledger with reason, backup
scrub as a separate act, resurrection guard. Fixture-driven; never touches the real archive.

The cascade's FTS ordering is load-bearing: content must be de-indexed via the
external-content 'delete' command BEFORE its rows vanish (migration 0002 trap) — the
index-still-works assertions after every delete are the corruption canary."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from introspect import deletion
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.run import run_import
from introspect.models import (
    ChatSession,
    ContentBlock,
    DeletionLedger,
    Favorite,
    Message,
    ParseAnomaly,
    Project,
    RawRecord,
    SessionEvent,
    SourceFile,
    TokenUsage,
    Transcript,
)
from introspect.search import get_search_index
from tests.conftest import PROJECT_SLUG_1, SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3

idx = get_search_index()


def _populated(tmp_path: Path, fixture_tree: Path):
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    engine = get_engine(dbp)
    upgrade_to_head(engine)
    factory = session_factory(engine)
    with factory() as db:
        idx.rebuild(db)
        db.commit()
    return dbp, factory


def _table_counts(db: Session, session_uuid: str) -> dict[str, int]:
    """Row counts scoped to one session across every cascade table."""
    t_ids = [t.id for t in db.query(Transcript).filter_by(session_id=session_uuid)]
    m_ids = [
        m.id for m in db.query(Message).filter(Message.transcript_id.in_(t_ids or [-1]))
    ]
    rr_ids = [
        r.id for r in db.query(RawRecord).filter(RawRecord.transcript_id.in_(t_ids or [-1]))
    ]
    sf_ids = [
        s.id for s in db.query(SourceFile).filter(SourceFile.transcript_id.in_(t_ids or [-1]))
    ]
    return {
        "sessions": db.query(ChatSession).filter_by(session_uuid=session_uuid).count(),
        "transcripts": len(t_ids),
        "messages": len(m_ids),
        "raw_records": len(rr_ids),
        "source_files": len(sf_ids),
        "content_blocks": db.query(ContentBlock)
        .filter(ContentBlock.message_id.in_(m_ids or [-1]))
        .count(),
        "token_usage": db.query(TokenUsage)
        .filter(TokenUsage.message_id.in_(m_ids or [-1]))
        .count(),
        "session_events": db.query(SessionEvent)
        .filter(SessionEvent.raw_record_id.in_(rr_ids or [-1]))
        .count(),
        "anomalies": db.query(ParseAnomaly)
        .filter(
            (ParseAnomaly.raw_record_id.in_(rr_ids or [-1]))
            | (ParseAnomaly.source_file_id.in_(sf_ids or [-1]))
        )
        .count(),
    }


def test_delete_session_cascades_completely_and_index_survives(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        before = _table_counts(db, SESSION_UUID_1)
        assert before["sessions"] == 1 and before["raw_records"] > 0
        canary_before = _table_counts(db, SESSION_UUID_3)

        outcome = deletion.delete_session(db, SESSION_UUID_1, reason="test scrub")
        assert outcome is not None
        assert outcome.records_deleted == before["raw_records"]

        after = _table_counts(db, SESSION_UUID_1)
        assert all(v == 0 for v in after.values()), f"orphans: {after}"
        # canary: the other project's session is untouched...
        assert _table_counts(db, SESSION_UUID_3) == canary_before
        # ...and the FTS index is both scrubbed AND intact (the 0002 corruption trap):
        assert idx.search(db, "horizon", sources=None) == ([], 0)  # deleted content gone
        hits, total = idx.search(db, "synthetic", sources=None)  # other content findable
        assert total >= 1 and hits


def test_delete_session_writes_ledger_with_reason_and_label(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        deletion.delete_session(db, SESSION_UUID_1, reason="client contract forbids retention")
        row = db.query(DeletionLedger).one()
        assert row.kind == "session"
        assert row.target == SESSION_UUID_1
        assert row.reason == "client contract forbids retention"
        assert row.sessions_deleted == 1
        assert row.records_deleted > 0
        assert row.label  # display title captured before the session row died


def test_delete_session_reason_may_be_declined(tmp_path: Path, fixture_tree: Path) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        deletion.delete_session(db, SESSION_UUID_1, reason=None)
        assert db.query(DeletionLedger).one().reason is None


def test_delete_unknown_session_returns_none_and_no_ledger(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        assert deletion.delete_session(db, "not-a-real-uuid", reason=None) is None
        assert db.query(DeletionLedger).count() == 0


def test_delete_session_removes_user_rows(tmp_path: Path, fixture_tree: Path) -> None:
    from introspect.ingest.capture import utcnow
    from introspect.models import ArchivedSession, UserTitle

    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        db.add(Favorite(session_uuid=SESSION_UUID_1, created_at=utcnow()))
        db.add(UserTitle(session_uuid=SESSION_UUID_1, title="t", updated_at=utcnow()))
        db.add(ArchivedSession(session_uuid=SESSION_UUID_1, created_at=utcnow()))
        db.commit()
        deletion.delete_session(db, SESSION_UUID_1, reason=None)
        assert db.query(Favorite).count() == 0
        assert db.query(UserTitle).count() == 0
        assert db.query(ArchivedSession).count() == 0


def test_delete_project_cascades_all_sessions_and_project_row(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        # PROJECT_SLUG_1 holds sessions 1 and 2; session 3 lives in the other project.
        outcome = deletion.delete_project(db, PROJECT_SLUG_1, reason="whole client gone")
        assert outcome is not None and outcome.sessions_deleted == 2
        assert db.query(Project).filter_by(dir_slug=PROJECT_SLUG_1).count() == 0
        for uuid in (SESSION_UUID_1, SESSION_UUID_2):
            assert all(v == 0 for v in _table_counts(db, uuid).values())
        assert _table_counts(db, SESSION_UUID_3)["sessions"] == 1
        row = db.query(DeletionLedger).one()
        assert row.kind == "project" and row.target == PROJECT_SLUG_1
        assert row.sessions_deleted == 2


def test_preview_counts_locations_and_resurrection_warning(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        pv = deletion.preview_session(db, SESSION_UUID_1)
        assert pv is not None
        assert pv.records > 0 and pv.sessions == 1
        # fixture source files genuinely exist on disk -> resurrection risk is real
        assert pv.source_paths_on_disk
        assert all(Path(p).exists() for p in pv.source_paths_on_disk)


def test_backup_scrub_is_separate_and_handles_immutable(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    backups = dbp.parent / "backups"
    backups.mkdir()
    bpath = backups / "pre-delete-copy.db"
    # A WAL-mode DB is main-file + WAL; a bare copyfile of the main file alone is an
    # INCOMPLETE copy. Checkpoint first — the same rule real make-a-copy backups need.
    deletion.finalize_scrub(get_engine(dbp))
    shutil.copyfile(dbp, bpath)
    if sys.platform == "darwin":  # exercise the uchg lift/restore path where it exists
        os.chflags(bpath, os.stat(bpath).st_flags | stat.UF_IMMUTABLE)

    try:
        with factory() as db:
            deletion.delete_session(db, SESSION_UUID_1, reason="scrub me")
        # main deletion NEVER touched the backup (triple-sure rule): session still there
        assert deletion.list_backup_dbs(dbp) == [bpath]
        b_factory = session_factory(get_engine(bpath))
        with b_factory() as bdb:
            assert bdb.query(ChatSession).filter_by(session_uuid=SESSION_UUID_1).count() == 1

        results = deletion.scrub_backups(dbp, "session", SESSION_UUID_1, reason="scrub me")
        assert [(p.name, ok) for p, ok in results] == [("pre-delete-copy.db", True)]
        with b_factory() as bdb:
            assert bdb.query(ChatSession).filter_by(session_uuid=SESSION_UUID_1).count() == 0
            assert bdb.query(DeletionLedger).count() == 1  # the backup remembers it forgot
        if sys.platform == "darwin":
            assert os.stat(bpath).st_flags & stat.UF_IMMUTABLE  # immutability restored
    finally:
        # tmp-dir hygiene: an immutable file outlives pytest's cleanup and accumulates
        # in the temp folder forever -- always clear the flag before the test ends.
        if sys.platform == "darwin":
            os.chflags(bpath, os.stat(bpath).st_flags & ~stat.UF_IMMUTABLE)


def test_finalize_scrub_runs_vacuum_and_checkpoint(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp, factory = _populated(tmp_path, fixture_tree)
    with factory() as db:
        deletion.delete_session(db, SESSION_UUID_1, reason=None)
    deletion.finalize_scrub(get_engine(dbp))  # must not raise (VACUUM outside txn)
