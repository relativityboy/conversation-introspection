"""Migration 0006 tests (walk-fix Task 9a): the ``content_blocks(message_id)`` index.

Binding-contract style mirrors ``test_migration_0004.py``/``test_migration_0003.py``: index
exists after upgrade, downgrade drops it. This index makes the ``chat_only`` trim's
correlated EXISTS-over-``content_blocks`` subquery (``_chat_only_filter`` in
``introspect.api.routes.sessions``) an index seek instead of a full-table scan per message.
"""

from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


def test_migration_0006_creates_content_blocks_message_id_index(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    insp = inspect(engine)
    indexes = {ix["name"]: ix for ix in insp.get_indexes("content_blocks")}
    assert "ix_content_blocks_message_id" in indexes
    assert indexes["ix_content_blocks_message_id"]["column_names"] == ["message_id"]


def test_migration_0006_downgrade_drops_index(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0005")
    names = {ix["name"] for ix in inspect(engine).get_indexes("content_blocks")}
    assert "ix_content_blocks_message_id" not in names
