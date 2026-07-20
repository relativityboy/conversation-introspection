"""Migration 0004 tests (Task P4-F3): the ``archived_sessions`` table.

Binding-contract style mirrors ``test_migration_0003.py`` (the ``user_titles`` table): table/PK/
FK/columns exist after upgrade, downgrade drops it. Like 0003 -- and unlike 0002's FTS5/primary-
uniqueness preflights -- ``archived_sessions`` is a plain table with a foreign key, so nothing in
this migration can fail against a populated archive and there are no preflight-helper tests.
"""

from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


def test_migration_0004_creates_archived_sessions_table(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    insp = inspect(engine)
    names = set(insp.get_table_names())
    assert "archived_sessions" in names

    pk = insp.get_pk_constraint("archived_sessions")
    assert pk["constrained_columns"] == ["session_uuid"]

    fks = insp.get_foreign_keys("archived_sessions")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "sessions"
    assert fks[0]["referred_columns"] == ["session_uuid"]
    assert fks[0]["constrained_columns"] == ["session_uuid"]

    columns = {c["name"]: c for c in insp.get_columns("archived_sessions")}
    assert set(columns) == {"session_uuid", "created_at"}
    assert columns["created_at"]["nullable"] is False


def test_migration_0004_downgrade_drops_archived_sessions_table(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0003")
    names = set(inspect(engine).get_table_names())
    assert "archived_sessions" not in names
