"""Search-index maintenance wired into the ingest paths (Task P2-3).

Phase 1 captured and interpreted transcripts but never touched the FTS5 index — it was only
ever built by an explicit ``rebuild``. This suite pins the Phase 2 wiring:

* capture indexes a message's new text blocks as it interprets them, in the SAME transaction
  as the rows (so the index rows commit/roll back atomically with their content);
* a divergence cleanup de-indexes the demoted generation's blocks BEFORE deleting their rows
  (the FTS5 external-content trap — de-indexing needs the still-present text);
* ``reparse`` clears the index and lets ``apply`` rebuild it, so ``introspect reparse`` also
  fully backfills the index for a pre-Phase-2 archive (no separate backfill command).

Every correctness check goes through ``SearchIndex.search`` / MATCH — never COUNT(*)/SELECT *
on ``content_fts`` — because an external-content FTS5 table's non-MATCH reads are served live
from ``content_blocks`` and do NOT reflect the shadow index (see fts5.py's NOTE).
"""

from introspect.ingest import interpret
from introspect.ingest.discovery import discover
from introspect.ingest.reparse import reparse_all
from introspect.search import get_search_index
from tests.test_capture import _capture_all

# The assistant line "still water runs deep beneath the surface" is the one block shared
# byte-for-byte across a divergence's old and new generations (only the first line is
# rewritten), so it is the probe for cross-generation de-index correctness.
SHARED_ASSISTANT_PHRASE = "still water"


def test_capture_indexes_new_text_blocks(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    hits, total = get_search_index().search(db_session, "horizon")
    assert total >= 1


def test_divergence_cleanup_deindexes_old_generation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    # Divergence recipe copied verbatim from
    # tests/test_capture_integrity.py::test_divergence_detected_and_regenerated:
    # rewrite the first line, keep the original tail, re-capture.
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-new1"}\n'
        + content[content.index(b"\n") + 1:]
    )
    _capture_all(db_session, fixture_tree)

    hits, _ = get_search_index().search(db_session, "REWRITTEN")
    assert hits  # new generation searchable

    # The old generation's blocks were de-indexed BEFORE its rows were deleted, so the shared
    # assistant phrase resolves once (the new generation only) — no duplicate/orphan blocks.
    all_hits, total = get_search_index().search(db_session, SHARED_ASSISTANT_PHRASE)
    assert len({h.block_id for h in all_hits}) == len(all_hits)  # no duplicate blocks
    assert total == 1  # found once, not twice across generations


def test_reparse_rebuilds_index_identically(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    _, before = get_search_index().search(db_session, "horizon")
    reparse_all(db_session)
    _, after = get_search_index().search(db_session, "horizon")
    assert before == after


def test_interpret_failure_rolls_back_index_with_rows(db_session, fixture_tree, monkeypatch):
    """An apply() that indexes a block then raises must roll the index row back WITH the block.

    Pattern from tests/test_capture.py::test_interpret_failure_never_rolls_back_capture:
    monkeypatch interpret.apply. Here it runs the real apply (which now creates + indexes the
    block) then raises for every ``user`` record; capture's ``_interpret_chunk`` rolls that
    record back, and its index row must die with it. "horizon" lives only in a user block, so
    it is unfindable afterward.
    """
    real_apply = interpret.apply

    def apply_then_boom(db, pr, raw):
        real_apply(db, pr, raw)  # creates the block AND stages its index row (Task P2-3)
        if pr.record_type == "user":
            raise RuntimeError("synthetic failure after the user block was indexed")

    monkeypatch.setattr(interpret, "apply", apply_then_boom)
    _capture_all(db_session, fixture_tree)

    _, total = get_search_index().search(db_session, "horizon")
    assert total == 0  # the rolled-back block and its index row died together


def test_human_queued_command_is_interpreted_and_searchable(db_session, tmp_path):
    """A human-origin queued_command flows through capture -> interpret -> FTS (Task P4-F1).

    These records were previously empty SYSTEM stubs (zero blocks, absent from search). The
    schema/3 blocks() override materializes the prompt as a text ContentBlock, which the normal
    capture-time indexing path then makes findable — no separate wiring, no rebuild needed.
    """
    from introspect.models import ContentBlock, Message
    from tests.conftest import _ingest_single_line
    from tests.fixtures.records import make_queued_command_line

    token = "phosphorescence drifting on the queued midnight tide"
    _ingest_single_line(db_session, tmp_path, make_queued_command_line(prompt=token))

    # Interpreted: exactly one attachment Message carrying one text ContentBlock with the prompt.
    msg = db_session.query(Message).filter_by(type="attachment").one()
    block = db_session.query(ContentBlock).filter_by(message_id=msg.id).one()
    assert block.block_kind == "text" and block.text_content == token

    # Searchable: capture-time indexing already put the prompt in the FTS index.
    _, total = get_search_index().search(db_session, "phosphorescence")
    assert total >= 1


def test_furniture_queued_command_stays_zero_block_and_unsearchable(db_session, tmp_path):
    """The non-human furniture variant is interpreted to a blockless Message, absent from FTS."""
    from introspect.models import ContentBlock, Message
    from tests.conftest import _ingest_single_line
    from tests.fixtures.records import make_queued_command_line

    token = "bioluminescent furniture that must never be indexed"
    _ingest_single_line(
        db_session, tmp_path, make_queued_command_line(human=False, prompt=token)
    )

    msg = db_session.query(Message).filter_by(type="attachment").one()
    assert db_session.query(ContentBlock).filter_by(message_id=msg.id).count() == 0
    _, total = get_search_index().search(db_session, "bioluminescent")
    assert total == 0
