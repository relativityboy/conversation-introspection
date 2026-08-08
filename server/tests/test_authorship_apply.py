"""Ingest post-pass tests (Task 3): ``classify_pending`` backfills authorship after
interpretation, driven by ``authorship_kind IS NULL``.

``ingested_db`` builds a main transcript (typed human record, a tool_result record whose
tool_use appears AFTER it in file order -- the out-of-order production case -- and a
Skill-injection record) plus a subagent transcript (a dispatch opener), using the repo's
existing ingest-fixture idiom: write synthetic ``.jsonl`` files under a project tree, then
``discover`` + ``capture_file`` each one into a migrated temp DB (see tests/conftest.py's
``fixture_tree``/``db_session`` and tests/test_reparse.py's inline-tree tests).
"""

import pytest
import sqlalchemy as sa

from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from tests.fixtures.records import (
    make_assistant_line,
    make_session_file,
    make_tool_result_user_line,
    make_user_line,
)

SESSION_UUID = "77777777-7777-4777-8777-777777777777"
BASH_TOOL_USE_ID = "toolu_bash0001"
SKILL_TOOL_USE_ID = "toolu_skill0001"


def _build_authorship_tree(root):
    proj = root / "-Users-x-authorship"
    proj.mkdir(parents=True)

    main_lines = [
        make_user_line(
            text="please continue",
            promptSource="typed",
            origin={"kind": "human"},
            sessionId=SESSION_UUID,
        ),
        # Out-of-order production case: this tool_result's tool_use (below, in the
        # assistant record) has not been seen yet at this point in file order.
        make_tool_result_user_line(tool_use_id=BASH_TOOL_USE_ID, sessionId=SESSION_UUID),
        make_user_line(
            content=[{"type": "text", "text": "Base directory for this skill: ..."}],
            isMeta=True,
            sourceToolUseID=SKILL_TOOL_USE_ID,
            sessionId=SESSION_UUID,
        ),
        make_assistant_line(
            with_tool_use=True,
            tool_use_id=BASH_TOOL_USE_ID,
            extra_blocks=[
                {
                    "type": "tool_use",
                    "id": SKILL_TOOL_USE_ID,
                    "name": "Skill",
                    "input": {"skill": "superpowers:brainstorming"},
                }
            ],
            sessionId=SESSION_UUID,
        ),
    ]
    (proj / f"{SESSION_UUID}.jsonl").write_bytes(make_session_file(main_lines))

    subagents_dir = proj / SESSION_UUID / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / "agent-abc123.jsonl").write_bytes(
        make_session_file(
            [make_user_line(text="You are implementing Task 3 of ...", sessionId=SESSION_UUID)]
        )
    )


def _ingest_authorship_tree(db, tmp_path):
    root = tmp_path / "authorship"
    _build_authorship_tree(root)
    for f in discover(root):
        capture_file(db, f)
    db.commit()
    return db


@pytest.fixture
def ingested_db(db_session, tmp_path):
    return _ingest_authorship_tree(db_session, tmp_path)


def test_classify_pending_populates_and_is_idempotent(ingested_db):
    from introspect.ingest.interpret import classify_pending

    census = classify_pending(ingested_db)
    assert census["human_typed"] == 1
    assert census["tool_result"] == 1
    assert census["skill_injection"] == 1
    assert census["dispatch"] == 1
    row = ingested_db.execute(
        sa.text("SELECT authorship_detail FROM messages WHERE authorship_kind='tool_result'")
    ).scalar_one()
    assert row == "Bash"  # resolved DESPITE the tool_use appearing later in file order
    assert classify_pending(ingested_db).total() == 0  # idempotent: nothing left NULL
