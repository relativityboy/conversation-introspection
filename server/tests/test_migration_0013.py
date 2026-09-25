"""Migration 0013 tests: project names re-derived from root sessions only.

Before 1.15.1, capture set ``projects.resolved_cwd`` from the first captured record carrying a
cwd -- which could be a subagent's, when the subagent ran in another directory and its file was
captured before the main one. 0013 converges existing archives: each project's resolved_cwd
becomes the first cwd seen on its main (root) transcripts, or NULL when no root record has one.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from introspect.db import get_engine, session_factory
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from tests.conftest import SESSION_UUID_1, SESSION_UUID_2
from tests.fixtures.records import make_user_line

_SERVER = Path(__file__).resolve().parents[1]


def _upgrade(engine, revision: str) -> None:
    cfg = AlembicConfig(str(_SERVER / "alembic.ini"))
    cfg.set_main_option("script_location", str(_SERVER / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def _write(path: Path, *lines: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(lines))


def test_migration_0013_rederives_project_cwd_from_root_sessions(tmp_path: Path) -> None:
    root = tmp_path / "transcripts"
    # Project A: root ran in /root-a, its subagent in /elsewhere (the reported bug's shape).
    _write(root / "-root-a" / f"{SESSION_UUID_1}.jsonl", make_user_line(cwd="/root-a"))
    _write(
        root / "-root-a" / SESSION_UUID_1 / "subagents" / "agent-abc123.jsonl",
        make_user_line(cwd="/elsewhere"),
    )
    # Project B: already correct -- must be left alone.
    _write(root / "-root-b" / f"{SESSION_UUID_2}.jsonl", make_user_line(cwd="/root-b"))

    engine = get_engine(tmp_path / "archive.db")
    _upgrade(engine, "0012")
    with session_factory(engine)() as db:
        for f in discover(root):
            capture_file(db, f)

    with engine.begin() as conn:
        # Simulate the pre-1.15.1 archive: project A named by its subagent.
        conn.execute(
            text("UPDATE projects SET resolved_cwd='/elsewhere' WHERE dir_slug='-root-a'")
        )
        # Project C: a stale name with no root record to back it (only a subagent-derived value).
        conn.execute(
            text(
                "INSERT INTO projects (dir_slug, resolved_cwd, first_seen_at) "
                "VALUES ('-root-c', '/elsewhere', '2026-01-01T00:00:00+00:00')"
            )
        )

    _upgrade(engine, "0013")

    with engine.connect() as conn:
        names = dict(conn.execute(text("SELECT dir_slug, resolved_cwd FROM projects")).all())
    assert names == {"-root-a": "/root-a", "-root-b": "/root-b", "-root-c": None}
