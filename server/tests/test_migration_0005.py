"""Migration 0005 tests (Task P4-F7): the ``schema_versions`` table + historical backfill.

Unlike 0003/0004 (plain tables), 0005 also BACKFILLS rows for the historical schema
generations (introspect-schema/1..3) at migration time, deriving ``first_encountered_at``
from the earliest ``import_runs`` evidence when the archive has any, else the migration run
time. These tests pin: table/PK/columns after upgrade, the three backfilled rows exist with a
non-null timestamp + diff_note, the earliest-import-run evidence path, the no-evidence
fallback, and that downgrade drops the table.
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from introspect.db import get_engine, upgrade_to_head


def _alembic_cfg():
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    return cfg


def test_migration_0005_creates_schema_versions_table(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    insp = inspect(engine)
    assert "schema_versions" in set(insp.get_table_names())

    pk = insp.get_pk_constraint("schema_versions")
    assert pk["constrained_columns"] == ["version"]

    columns = {c["name"]: c for c in insp.get_columns("schema_versions")}
    assert set(columns) == {"version", "first_encountered_at", "diff_note"}
    assert columns["first_encountered_at"]["nullable"] is False


def test_migration_0005_backfills_historical_versions(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT version, first_encountered_at, diff_note FROM schema_versions "
                "ORDER BY version"
            )
        ).all()
    versions = [r[0] for r in rows]
    assert versions == [
        "introspect-schema/1",
        "introspect-schema/2",
        "introspect-schema/3",
    ]
    for _version, first_at, diff_note in rows:
        assert first_at  # non-null
        assert diff_note  # a real human-readable note


def test_migration_0005_backfill_uses_earliest_import_run(tmp_path: Path) -> None:
    """When the archive already has import history, the historical versions inherit the
    earliest import run's ``started_at`` (the best first-encounter evidence available)."""
    from alembic import command

    engine = get_engine(tmp_path / "t.db")
    cfg = _alembic_cfg()
    # Bring the DB up to 0004 (before schema_versions exists), then seed an import run.
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0004")
    earliest = "2026-01-02T03:04:05.000000+00:00"
    later = "2026-05-06T07:08:09.000000+00:00"
    with engine.begin() as conn:
        for i, started in enumerate((later, earliest), start=1):
            conn.execute(
                text(
                    "INSERT INTO import_runs (id, trigger, started_at, files_seen, "
                    "records_added, records_skipped_duplicate, anomaly_count, status) "
                    "VALUES (:id, 'cli', :started, 0, 0, 0, 0, 'ok')"
                ),
                {"id": i, "started": started},
            )
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0005")
    with engine.connect() as conn:
        firsts = {
            v: f
            for v, f in conn.execute(
                text("SELECT version, first_encountered_at FROM schema_versions")
            ).all()
        }
    assert firsts["introspect-schema/1"] == earliest
    assert firsts["introspect-schema/2"] == earliest
    assert firsts["introspect-schema/3"] == earliest


def test_migration_0005_backfill_falls_back_to_migration_time(tmp_path: Path) -> None:
    """A fresh archive with no import history backfills a real (migration-time) timestamp."""
    before = datetime.now(timezone.utc)
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    with engine.connect() as conn:
        firsts = [
            f for (f,) in conn.execute(text("SELECT first_encountered_at FROM schema_versions")).all()
        ]
    assert firsts and all(firsts)
    # Every backfilled timestamp parses as an aware UTC datetime at/after the test start.
    for f in firsts:
        dt = datetime.fromisoformat(f)
        assert dt.tzinfo is not None
        assert dt >= before.replace(microsecond=0) - _one_second()


def _one_second():
    from datetime import timedelta

    return timedelta(seconds=1)


def test_migration_0005_downgrade_drops_schema_versions_table(tmp_path: Path) -> None:
    from alembic import command

    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    cfg = _alembic_cfg()
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0004")
    assert "schema_versions" not in set(inspect(engine).get_table_names())
