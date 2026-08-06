"""Tests for schema-version provenance (Task P4-F7, Part 3).

Covers the idempotent recorder that stamps the RUNNING ``SCHEMA_VERSION`` into the
``schema_versions`` table the first time this codebase runs an import/reparse against a DB,
its wiring into ``run_import`` and ``reparse_all``, and the ``introspect status`` surface
(current version + count of known versions).
"""

from pathlib import Path

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import run_import
from introspect.models import SchemaVersion
from introspect.schema import DIFF_NOTES, SCHEMA_VERSION
from introspect.schema_versions import ensure_current_schema_version_recorded
from introspect.status import collect_status, schema_line


def _fresh_db(tmp_path: Path):
    engine = get_engine(tmp_path / "a.db")
    upgrade_to_head(engine)
    return engine


def test_recorder_inserts_current_version_row(tmp_path: Path) -> None:
    engine = _fresh_db(tmp_path)
    with session_factory(engine)() as db:
        ensure_current_schema_version_recorded(db)
        row = db.get(SchemaVersion, SCHEMA_VERSION)
        assert row is not None
        assert row.first_encountered_at is not None
        assert row.diff_note == DIFF_NOTES[SCHEMA_VERSION]


def test_recorder_is_idempotent(tmp_path: Path) -> None:
    engine = _fresh_db(tmp_path)
    with session_factory(engine)() as db:
        ensure_current_schema_version_recorded(db)
        first_at = db.get(SchemaVersion, SCHEMA_VERSION).first_encountered_at
        # Second call must NOT insert a duplicate nor move the timestamp.
        ensure_current_schema_version_recorded(db)
        rows = db.query(SchemaVersion).filter_by(version=SCHEMA_VERSION).all()
        assert len(rows) == 1
        assert rows[0].first_encountered_at == first_at


def test_import_records_current_schema_version(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        assert db.get(SchemaVersion, SCHEMA_VERSION) is not None


def test_reparse_records_current_schema_version(tmp_path: Path) -> None:
    engine = _fresh_db(tmp_path)
    with session_factory(engine)() as db:
        reparse_all(db)
        assert db.get(SchemaVersion, SCHEMA_VERSION) is not None


def test_status_reports_schema_version_and_known_count(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        snap = collect_status(db)
    assert snap.schema_version == SCHEMA_VERSION
    # Fresh-DB count is bump-invariant: migration 0005 backfills /1-/3 (frozen), ensure_current_schema_version_recorded adds exactly one row for the CURRENT version, total = 4 regardless of generation.
    assert snap.schema_versions_known == 4
    line = schema_line(snap)
    assert SCHEMA_VERSION in line
    assert "known versions: 4" in line


def test_status_on_fresh_db_counts_only_backfilled(tmp_path: Path) -> None:
    # A migrated-but-never-imported DB has the 3 backfilled historical rows and no current row
    # yet (the current version is recorded only when import/reparse first runs).
    engine = _fresh_db(tmp_path)
    with session_factory(engine)() as db:
        snap = collect_status(db)
    assert snap.schema_version == SCHEMA_VERSION
    assert snap.schema_versions_known == 3
