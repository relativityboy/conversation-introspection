"""Migration 0007 tests: the ``raw_records.reassembled`` column.

Binding-contract style mirrors ``test_migration_0006.py``: column exists after
upgrade with correct nullability and default, downgrade drops it. This column marks
capture provenance (spec §2): ``reassembled`` records are always distinguishable from
native single-line captures (Task 3 sets it True for multi-line units; Task 5 copies
it through recapture).
"""

from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


def test_migration_0007_adds_reassembled_column(tmp_path: Path) -> None:
    """Capture metadata for spec §2's provenance marker: reassembled records are
    always distinguishable from native single-line captures."""
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("raw_records")}
    assert "reassembled" in cols
    assert cols["reassembled"]["nullable"] is False


def test_migration_0007_downgrade_drops_column(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0006")
    cols = {c["name"]: c for c in inspect(engine).get_columns("raw_records")}
    assert "reassembled" not in cols
