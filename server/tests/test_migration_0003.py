"""Migration 0003 tests (Task P4-1): the ``user_titles`` table.

Binding-contract style mirrors ``test_migration_0002.py``'s first test: table/PK/FK exist
after upgrade, downgrade drops it. No preflight-helper tests here (unlike 0002's FTS5/
primary-uniqueness checks) -- ``user_titles`` is a plain table with a foreign key, nothing in
this migration can fail against a populated archive.
"""

from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


def test_migration_0003_creates_user_titles_table(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    insp = inspect(engine)
    names = set(insp.get_table_names())
    assert "user_titles" in names

    pk = insp.get_pk_constraint("user_titles")
    assert pk["constrained_columns"] == ["session_uuid"]

    fks = insp.get_foreign_keys("user_titles")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "sessions"
    assert fks[0]["referred_columns"] == ["session_uuid"]
    assert fks[0]["constrained_columns"] == ["session_uuid"]

    columns = {c["name"]: c for c in insp.get_columns("user_titles")}
    assert set(columns) == {"session_uuid", "title", "updated_at"}
    assert columns["title"]["nullable"] is False


def test_migration_0003_downgrade_drops_user_titles_table(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0002")
    names = set(inspect(engine).get_table_names())
    assert "user_titles" not in names
