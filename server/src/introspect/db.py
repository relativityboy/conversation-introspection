"""Engine / session factories, Alembic driver, and the UTC datetime column type.

Everything datetime-shaped in this project is stored as ISO-8601 UTC *text* via
:class:`UTCDateTime`. SQLite's native ``DateTime`` round-trips naive values, which
later crashes aware/naive comparisons in fresh processes (Opus review M2); text also
keeps behavior identical when the archive is ported to Postgres.
"""

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, String, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Store tz-aware datetimes as ISO-8601 UTC text; return tz-aware UTC datetimes.

    Naive inputs are assumed to already be UTC. This is used for EVERY datetime
    column in the schema so reads are always timezone-aware regardless of backend.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


def get_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def upgrade_to_head(engine: Engine) -> None:
    cfg = AlembicConfig(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    cfg.attributes["connection"] = engine.connect()
    try:
        command.upgrade(cfg, "head")
    finally:
        cfg.attributes["connection"].close()
