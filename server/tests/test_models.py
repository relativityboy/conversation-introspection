from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


EXPECTED_TABLES = {
    "projects", "sessions", "transcripts", "source_files", "raw_records",
    "import_runs", "parse_anomalies", "messages", "content_blocks",
    "token_usage", "session_events",
}


def test_migration_creates_all_tables(tmp_path: Path):
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_wal_mode_enabled(tmp_path: Path):
    engine = get_engine(tmp_path / "t.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
