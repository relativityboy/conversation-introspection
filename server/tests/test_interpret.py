"""Interpretation tests (Task 8): ParseResult -> Message / ContentBlock / TokenUsage /
SessionEvent, session time-bound folding, and cross-generation de-duplication.

Interpretation runs downstream of capture in its own transaction; it turns a parsed record
into the normalized reading-room rows. The first six tests are the binding contract (verbatim
from task-8-brief); the remainder pin the M2 aware-datetime fold across a fresh engine, the
resolved_cwd backfill, and the divergence-cleanup invariant (exactly one Message per uuid).
"""

from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.models import (
    ChatSession,
    ContentBlock,
    Message,
    Project,
    SessionEvent,
    TokenUsage,
    Transcript,
)
from tests.conftest import SESSION_UUID_1
from tests.fixtures.records import make_user_line
from tests.test_capture import _capture_all

# --- Binding contract (verbatim from task-8-brief) --------------------------------------


def test_user_message_and_block(db_session, ingested_user_raw):
    msg = db_session.query(Message).one()
    assert msg.type == "user" and msg.record_uuid
    blocks = db_session.query(ContentBlock).filter_by(message_id=msg.id).all()
    assert blocks[0].block_kind == "text" and blocks[0].text_content == "hello world"


def test_assistant_thinking_block_kind_no_text(db_session, ingested_assistant_raw):
    kinds = {b.block_kind for b in db_session.query(ContentBlock).all()}
    assert "thinking" in kinds
    tb = db_session.query(ContentBlock).filter_by(block_kind="thinking").one()
    assert tb.text_content in (None, "")  # CLI never persists thinking text


def test_usage_row(db_session, ingested_assistant_raw):
    assert db_session.query(TokenUsage).count() == 1


def test_title_event_updates_session_cache(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    s = db_session.query(ChatSession).filter(ChatSession.ai_title.isnot(None)).first()
    assert s is not None
    assert db_session.query(SessionEvent).filter_by(event_kind="ai-title").count() >= 1


def test_session_time_bounds_folded(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    s = db_session.query(ChatSession).first()
    assert s.started_at <= s.last_activity_at


def test_file_history_snapshot_archive_only(db_session, ingested_snapshot_raw):
    assert db_session.query(Message).count() == 0
    assert (
        db_session.query(SessionEvent)
        .filter_by(event_kind="file-history-snapshot")
        .count()
        == 1
    )


# --- Additional required tests (Opus review M2 + resolved_cwd + divergence cleanup) ------


def test_time_fold_across_fresh_engine(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    engine = get_engine(dbp)
    upgrade_to_head(engine)
    with session_factory(engine)() as db:
        for f in discover(fixture_tree):
            capture_file(db, f)
        db.commit()
    engine.dispose()
    # Fresh engine + fresh session: DB-loaded datetimes must still compare/fold.
    engine2 = get_engine(dbp)
    with session_factory(engine2)() as db2:
        main = next(f for f in discover(fixture_tree) if f.kind == "main")
        with main.path.open("ab") as fh:
            fh.write(make_user_line(text="later prompt"))
        capture_file(db2, next(f for f in discover(fixture_tree) if f.path == main.path))
        db2.commit()
        s = db2.query(ChatSession).filter_by(session_uuid=main.session_uuid).one()
        assert s.started_at.tzinfo is not None and s.started_at <= s.last_activity_at


def test_project_resolved_cwd_populated(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    assert db_session.query(Project).filter(Project.resolved_cwd.isnot(None)).count() >= 1


def test_divergence_leaves_one_message_per_uuid(db_session, fixture_tree):
    """After a rewrite (divergence), the demoted generation's interpretation rows are dropped.

    The new generation re-ingests the same record_uuids (dedup bypassed); without the demotion
    cleanup, the unchanged lines would yield two Messages per uuid across generations.
    """
    _capture_all(db_session, fixture_tree)
    # Session 1's main file is the first main by path; it is the one we rewrite.
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    assert main.session_uuid == SESSION_UUID_1
    original = main.path.read_bytes()
    # Rewrite only the first line's bytes (changes the prefix -> divergence); the assistant
    # line keeps its original uuid, so it exists in BOTH generations' raw_records.
    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-div1"}\n'
        + original[original.index(b"\n") + 1 :]
    )
    _capture_all(db_session, fixture_tree)

    # Pin the query to the transcript that actually diverged (session 1's main).
    transcript = (
        db_session.query(Transcript)
        .filter_by(session_id=SESSION_UUID_1, kind="main", agent_hex_id=None)
        .one()
    )
    # Every record_uuid in this transcript maps to exactly one Message (no cross-gen dupes).
    uuids = [
        u
        for (u,) in db_session.query(Message.record_uuid).filter(
            Message.transcript_id == transcript.id
        )
    ]
    assert uuids  # the diverged transcript still has interpreted messages
    assert len(uuids) == len(set(uuids))
    # u-div1 is the REWRITTEN line's uuid: this just confirms the new generation was
    # interpreted. The real no-cross-generation-dupes guard is the len==set check above.
    assert "u-div1" in uuids
