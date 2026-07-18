"""Migration 0002 tests (Task 1): favorites, content_fts (create+backfill), and the
partial unique index enforcing at most one ``is_primary`` source file per transcript.

The first two tests are the binding contract (verbatim from task-1-brief). The remainder
cover the backfill predicate and unit-test the two preflight helpers directly, rather than
through a from-scratch non-FTS5 SQLite build (impractical to construct in CI) or a
populated-then-migrated archive (see ``test_migration_preflight_rejects_double_primary``
docstring for why the index is dropped via raw SQL instead).
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from introspect.db import get_engine, upgrade_to_head
from introspect.ingest.capture import utcnow
from introspect.models import ContentBlock, Message, SourceFile
from tests.test_capture import _capture_all

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0002_search_favorites.py"
)


def _load_migration_0002():
    """Load the migration module by path (its filename isn't a valid import identifier)."""
    spec = importlib.util.spec_from_file_location("_migration_0002_under_test", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Binding contract (verbatim from task-1-brief) ----------------------------------------


def test_migration_0002_creates_objects(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    names = set(inspect(engine).get_table_names())
    assert "favorites" in names
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='content_fts'").fetchone()
        assert row is not None
        idx = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='uq_one_primary_per_transcript'"
        ).fetchone()
        assert idx and "WHERE is_primary" in idx[0]


def test_partial_unique_index_enforces_single_primary(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    sf = db_session.query(SourceFile).filter_by(is_primary=True).first()
    dup = SourceFile(project_id=sf.project_id, transcript_id=sf.transcript_id,
                     path=sf.path + ".copy", kind="main", is_primary=True, generation=0,
                     byte_offset_checkpoint=0, last_size=0, prefix_hash="", status="active",
                     first_seen_at=utcnow(), last_seen_at=utcnow())
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- Backfill predicate ---------------------------------------------------------------------


# The exact backfill predicate under test, imported from the migration itself so this
# file can never drift from what 0002 actually runs.
_BACKFILL_SQL = _load_migration_0002()._BACKFILL_SQL


def test_migration_backfills_existing_blocks(db_session, fixture_tree):
    """Re-runs the exact backfill INSERT against a populated content_blocks table, then
    exercises a reindex-from-scratch (DELETE + re-INSERT) — the scenario the brief warns
    about: an empty index over a populated archive silently looking like data loss.

    ``db_session`` migrates through 0002 on an EMPTY database, so content_fts starts empty
    even after ``_capture_all`` populates content_blocks (the migration's own backfill ran
    before any data existed). The predicate is run once FIRST to bring content_fts in sync
    with content_blocks before deleting: content_fts is an external-content FTS5 table, and
    a bare ``DELETE FROM content_fts`` while the shadow index is out of sync with
    content_blocks corrupts the database file (verified empirically — see the NOTE(claude)
    on content_fts's creation in the migration). Search correctness is asserted via MATCH,
    not COUNT(*)/SELECT * — those are served live from content_blocks by rowid on an
    external-content table and do NOT reflect what's actually in the FTS index.
    """
    _capture_all(db_session, fixture_tree)
    conn = db_session.connection()
    conn.exec_driver_sql(_BACKFILL_SQL)  # first sync: index now matches content_blocks
    conn.exec_driver_sql("DELETE FROM content_fts")  # safe now that state is in sync
    conn.exec_driver_sql(_BACKFILL_SQL)  # re-backfill from scratch
    hits = conn.exec_driver_sql(
        "SELECT text_content FROM content_fts WHERE content_fts MATCH 'synthetic'"
    ).fetchall()
    assert any("synthetic user message" in h[0] for h in hits)


def test_migration_backfill_excludes_non_text_and_empty_blocks(db_session, fixture_tree):
    """The predicate is block_kind='text' AND text_content NOT NULL AND <>'' — nothing else.

    Verified via MATCH (the real FTS index), not COUNT(*)/SELECT * — on an external-content
    FTS5 table those are served live from content_blocks by rowid and would show every
    content_blocks row regardless of whether the predicate actually included it.
    """
    _capture_all(db_session, fixture_tree)
    msg = db_session.query(Message).first()
    db_session.add_all([
        ContentBlock(message_id=msg.id, block_index=99, block_kind="tool_use",
                     text_content="EXCLUDED_NONTEXT_MARKER", tool_name="Bash"),
        ContentBlock(message_id=msg.id, block_index=100, block_kind="text", text_content=""),
        ContentBlock(message_id=msg.id, block_index=101, block_kind="text", text_content=None),
    ])
    db_session.flush()

    conn = db_session.connection()
    conn.exec_driver_sql(_BACKFILL_SQL)  # content_fts is still empty here: a pure, safe insert
    hits = conn.exec_driver_sql(
        "SELECT text_content FROM content_fts WHERE content_fts MATCH 'EXCLUDED_NONTEXT_MARKER'"
    ).fetchall()
    assert hits == []


# --- Preflight helpers, unit-tested directly -------------------------------------------------


def test_fts5_probe_raises_clear_error_when_unavailable():
    migration = _load_migration_0002()

    class _NoFTS5Conn:
        def exec_driver_sql(self, sql):  # noqa: ANN001, ARG002
            raise sqlite3.OperationalError("no such module: fts5")

    with pytest.raises(RuntimeError, match="FTS5"):
        migration._check_fts5_available(_NoFTS5Conn())


def test_fts5_probe_passes_and_cleans_up_on_a_real_connection(db_session):
    migration = _load_migration_0002()
    conn = db_session.connection()
    migration._check_fts5_available(conn)  # must not raise
    assert conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE name='_fts5_probe'"
    ).fetchone() is None


def test_find_double_primary_transcripts_empty_when_invariant_holds(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    migration = _load_migration_0002()
    assert migration.find_double_primary_transcripts(db_session.connection()) == []


def test_migration_preflight_rejects_double_primary(db_session, fixture_tree):
    """Unit-tests the preflight helper against a synthetic double-primary state.

    The partial unique index this same migration creates would normally forbid two
    is_primary rows for one transcript, so it is dropped first via raw SQL to construct the
    state the preflight exists to catch (an archive migrated before this constraint existed,
    or one where the index was manually removed) — simpler and just as faithful as spinning
    up a separate pre-0002 database.
    """
    _capture_all(db_session, fixture_tree)
    conn = db_session.connection()
    conn.exec_driver_sql("DROP INDEX uq_one_primary_per_transcript")

    sf = db_session.query(SourceFile).filter_by(is_primary=True).first()
    dup = SourceFile(project_id=sf.project_id, transcript_id=sf.transcript_id,
                     path=sf.path + ".dup", kind="main", is_primary=True, generation=0,
                     byte_offset_checkpoint=0, last_size=0, prefix_hash="", status="active",
                     first_seen_at=utcnow(), last_seen_at=utcnow())
    db_session.add(dup)
    db_session.flush()

    migration = _load_migration_0002()
    dupes = migration.find_double_primary_transcripts(conn)
    assert sf.transcript_id in dupes


def test_downgrade_drops_new_objects(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")
    names = set(inspect(engine).get_table_names())
    assert "favorites" not in names
    assert "content_fts" not in names
    with engine.connect() as conn:
        idx = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='uq_one_primary_per_transcript'"
        ).fetchone()
        assert idx is None
