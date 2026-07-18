"""Shared pytest fixtures for the introspect test suite.

``fixture_tree`` builds a synthetic transcript directory tree that mirrors Claude Code's
on-disk layout (see task-4-brief.md). Its shape is a PINNED CONTRACT: later tasks hardcode
the slugs, session uuids, and agent identifiers defined here, so treat every constant below
as load-bearing, not incidental.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.search import get_search_index
from tests.fixtures.records import (
    make_assistant_line,
    make_session_file,
    make_snapshot_line,
    make_thin_meta_line,
    make_user_line,
)

# --- Pinned identifiers (later tasks hardcode these) -------------------------------------

PROJECT_SLUG_1 = "-Users-x-proj"  # 2 sessions
PROJECT_SLUG_2 = "-Users-x-proj2"  # 1 session

SESSION_UUID_1 = "11111111-1111-1111-1111-111111111111"
SESSION_UUID_2 = "22222222-2222-2222-2222-222222222222"
SESSION_UUID_3 = "33333333-3333-3333-3333-333333333333"

AGENT_HEX_ID = "abc123"
AGENT_TYPE = "Explore"
AGENT_TOOL_USE_ID = "toolu_fixture01"

BACKUP_EPOCH = 1720000000

# --- Fixture line content ------------------------------------------------------------------
# Precomputed once at import time: the bytes themselves are static per test session, only
# the destination tmp_path varies per test. Every main file has >=1 user + >=1 assistant
# line; session 1 additionally carries an ai-title and a file-history-snapshot line.

# NOTE(claude): session 1's user/assistant text carry distinctive, single-occurrence
# search phrases ("horizon" + the exact bigram "still water") the FTS5 tests (Task P2-2)
# rely on. They appear ONLY in session 1 so the session-scoping test can assert they are
# absent from other sessions. Sessions 2/3 keep the default "synthetic ..." text (search
# tests need a horizon-free session, and test_migration_0002 relies on at least one main
# user line still reading "synthetic user message"). Changing text is free for
# TOTAL_FIXTURE_LINES (it counts lines, not content); do NOT add or remove lines here.
_SESSION_1_LINES = [
    make_user_line(text="the horizon band maps hours to color", sessionId=SESSION_UUID_1),
    make_assistant_line(
        text="still water runs deep beneath the surface", sessionId=SESSION_UUID_1
    ),
    make_thin_meta_line("ai-title", session_id=SESSION_UUID_1),
    make_snapshot_line(session_id=SESSION_UUID_1),
]
_SESSION_2_LINES = [
    make_user_line(sessionId=SESSION_UUID_2),
    make_assistant_line(sessionId=SESSION_UUID_2),
]
_SESSION_3_LINES = [
    make_user_line(sessionId=SESSION_UUID_3),
    make_assistant_line(sessionId=SESSION_UUID_3),
]
_SUBAGENT_LINES = [
    make_user_line(text="synthetic subagent user message", sessionId=SESSION_UUID_1),
    make_assistant_line(text="synthetic subagent assistant reply", sessionId=SESSION_UUID_1),
]

# bak file duplicates the first 2 lines of session 1's main file (an older copy); dedup
# (a later task) skips every bak line, so it contributes 0 to TOTAL_FIXTURE_LINES.
_BACKUP_LINES = _SESSION_1_LINES[:2]

TOTAL_FIXTURE_LINES = (
    len(_SESSION_1_LINES) + len(_SESSION_2_LINES) + len(_SESSION_3_LINES) + len(_SUBAGENT_LINES)
)


@pytest.fixture
def fixture_tree(tmp_path: Path) -> Path:
    """Build the pinned synthetic transcript tree under a fresh ``tmp_path`` and return root.

    Layout::

        <root>/-Users-x-proj/11111111-...-111111111111.jsonl              (main, session 1)
        <root>/-Users-x-proj/11111111-...-111111111111.jsonl.bak-1720000000  (backup)
        <root>/-Users-x-proj/11111111-.../subagents/agent-abc123.jsonl    (subagent)
        <root>/-Users-x-proj/11111111-.../subagents/agent-abc123.meta.json
        <root>/-Users-x-proj/22222222-...-222222222222.jsonl              (main, session 2)
        <root>/-Users-x-proj2/33333333-...-333333333333.jsonl             (main, session 3)
    """
    root = tmp_path / "transcripts"

    proj1 = root / PROJECT_SLUG_1
    proj2 = root / PROJECT_SLUG_2
    proj1.mkdir(parents=True)
    proj2.mkdir(parents=True)

    (proj1 / f"{SESSION_UUID_1}.jsonl").write_bytes(make_session_file(_SESSION_1_LINES))
    (proj1 / f"{SESSION_UUID_2}.jsonl").write_bytes(make_session_file(_SESSION_2_LINES))
    (proj2 / f"{SESSION_UUID_3}.jsonl").write_bytes(make_session_file(_SESSION_3_LINES))

    (proj1 / f"{SESSION_UUID_1}.jsonl.bak-{BACKUP_EPOCH}").write_bytes(
        make_session_file(_BACKUP_LINES)
    )

    subagents_dir = proj1 / SESSION_UUID_1 / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / f"agent-{AGENT_HEX_ID}.jsonl").write_bytes(make_session_file(_SUBAGENT_LINES))
    (subagents_dir / f"agent-{AGENT_HEX_ID}.meta.json").write_text(
        json.dumps(
            {
                "agentType": AGENT_TYPE,
                "description": "Synthetic Explore agent spawned for fixture coverage.",
                "toolUseId": AGENT_TOOL_USE_ID,
            }
        )
    )

    return root


@pytest.fixture
def db_session(tmp_path: Path) -> Iterator[Session]:
    """A migrated, empty archive DB bound to a fresh session for one test.

    Uses ``upgrade_to_head`` (real Alembic migrations, not ``create_all``) so tests
    exercise the same schema production ingests do.
    """
    engine = get_engine(tmp_path / "archive.db")
    upgrade_to_head(engine)
    factory = session_factory(engine)
    with factory() as session:
        yield session


@pytest.fixture
def indexed_fixture(db_session: Session, fixture_tree: Path) -> Session:
    """Capture the pinned fixture tree, then build the FTS5 search index over it.

    Returns the same ``db_session`` (already populated + indexed) so search tests can
    query it directly. ``rebuild`` is the from-scratch path production uses after a bulk
    import, so this exercises the real indexing predicate over real interpreted rows.
    """
    for f in discover(fixture_tree):
        capture_file(db_session, f)
    db_session.commit()
    get_search_index().rebuild(db_session)
    db_session.commit()
    return db_session


# --- Single-line interpretation fixtures (Task 8) ----------------------------------------
# Each captures ONE synthetic line through the real capture_file pipeline, so interpretation
# has already produced its rows by the time the test queries them.

SINGLE_SESSION_UUID = "9e9e9e9e-0000-4000-8000-000000000009"


def _ingest_single_line(db: Session, tmp_path: Path, line: bytes) -> Session:
    """Write ``line`` as a one-line main transcript, capture it, and return the same session."""
    proj = tmp_path / "single" / "-Users-x-single"
    proj.mkdir(parents=True)
    (proj / f"{SINGLE_SESSION_UUID}.jsonl").write_bytes(line)
    for f in discover(tmp_path / "single"):
        capture_file(db, f)
    db.commit()
    return db


@pytest.fixture
def ingested_user_raw(db_session: Session, tmp_path: Path) -> Session:
    """One captured ``user`` line whose text body is exactly ``hello world``."""
    return _ingest_single_line(
        db_session, tmp_path, make_user_line(text="hello world", sessionId=SINGLE_SESSION_UUID)
    )


@pytest.fixture
def ingested_assistant_raw(db_session: Session, tmp_path: Path) -> Session:
    """One captured ``assistant`` line carrying a thinking block and default usage."""
    return _ingest_single_line(
        db_session,
        tmp_path,
        make_assistant_line(with_thinking=True, sessionId=SINGLE_SESSION_UUID),
    )


@pytest.fixture
def ingested_snapshot_raw(db_session: Session, tmp_path: Path) -> Session:
    """One captured ``file-history-snapshot`` line (a thin meta, never a Message)."""
    return _ingest_single_line(
        db_session, tmp_path, make_snapshot_line(session_id=SINGLE_SESSION_UUID)
    )
