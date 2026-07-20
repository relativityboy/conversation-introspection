"""FTS5 SearchIndex tests (Task P2-2): index / search / delete / rebuild + the sanitizer.

The binding contract (SearchHit shape, the SearchIndex protocol, the delete_for_blocks
external-content trap, and sanitize_query's never-raise property) is verbatim from
task-2-brief. Beyond the listed tests this file adds the required drift guard
(``test_index_predicate_matches_migration_backfill``): the search index and migration 0002
MUST index the same rows, and the two carry independent copies of the text-only predicate.

Search correctness is asserted via ``search`` / MATCH — never COUNT(*)/SELECT * — because
content_fts is an external-content FTS5 table whose non-MATCH reads are served live from
content_blocks by rowid and do NOT reflect the shadow index (see migration 0002's NOTE).
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from introspect.models import ContentBlock, Message
from introspect.search import SearchHit, get_search_index, sanitize_query
from introspect.search.fts5 import _TEXT_PREDICATE
from tests.conftest import (
    PROJECT_SLUG_1,
    PROJECT_SLUG_2,
    SESSION_UUID_1,
    SESSION_UUID_2,
    SESSION_UUID_3,
)

idx = get_search_index()

OTHER_SESSION = SESSION_UUID_2  # a session where "horizon" / "still water" never appear

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0002_search_favorites.py"
)


def _load_migration_0002():
    """Load migration 0002 by path (its filename isn't a valid import identifier)."""
    spec = importlib.util.spec_from_file_location("_migration_0002_for_search", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text_block_id(db, phrase: str) -> int:
    """Return the id of the (single) text content block whose text contains ``phrase``."""
    block = (
        db.query(ContentBlock)
        .filter(ContentBlock.block_kind == "text")
        .filter(ContentBlock.text_content.contains(phrase))
        .one()
    )
    return block.id


def _add_tool_use_block(db) -> int:
    """Attach a fresh tool_use block to an existing message and return its id (never indexed)."""
    msg = db.query(Message).first()
    block = ContentBlock(
        message_id=msg.id,
        block_index=999,
        block_kind="tool_use",
        text_content="ZZTOP_TOOLUSE_MARKER",  # would match if the predicate wrongly indexed it
        tool_name="Bash",
    )
    db.add(block)
    db.flush()
    return block.id


def _strip_marks(snippet: str) -> str:
    return snippet.replace("<mark>", "").replace("</mark>", "")


# --- Binding contract (verbatim from task-2-brief) ----------------------------------------


def test_index_and_search_roundtrip(db_session, indexed_fixture):
    hits, total = idx.search(db_session, "horizon")
    assert total >= 1 and "<mark>" in hits[0].snippet
    hit = hits[0]
    assert isinstance(hit, SearchHit)
    assert hit.session_uuid == SESSION_UUID_1
    assert hit.block_kind == "text"
    assert hit.block_id > 0 and hit.message_id > 0 and hit.transcript_id > 0
    assert "horizon" in _strip_marks(hit.snippet).lower()


def test_search_scoped_to_session(db_session, indexed_fixture):
    hits, _ = idx.search(db_session, "horizon", session_uuid=OTHER_SESSION)
    assert hits == []


def test_only_text_blocks_indexed(db_session, indexed_fixture):
    tool_use_block_id = _add_tool_use_block(db_session)
    n = idx.index_blocks(db_session, [tool_use_block_id])
    assert n == 0
    # It stayed out of the index: its marker is unfindable and nothing corrupted.
    hits, total = idx.search(db_session, "ZZTOP_TOOLUSE_MARKER")
    assert total == 0 and hits == []


def test_delete_for_blocks_removes_hits(db_session, indexed_fixture):
    """A mixed id list (indexed text + never-indexed tool_use + nonexistent id) deindexes
    ONLY the text block, returns 1, and corrupts nothing — the FTS5 external-content trap.
    """
    horizon_id = _text_block_id(db_session, "horizon")
    tool_use_id = _add_tool_use_block(db_session)
    nonexistent_id = 10_000_000

    removed = idx.delete_for_blocks(db_session, [horizon_id, tool_use_id, nonexistent_id])
    assert removed == 1

    # The horizon block is gone from the index...
    hits, total = idx.search(db_session, "horizon")
    assert total == 0 and hits == []

    # ...but the index is intact: another term still resolves and snippet() still works.
    still_hits, still_total = idx.search(db_session, "still water")
    assert still_total >= 1 and "<mark>" in still_hits[0].snippet


def test_rebuild_from_scratch_matches_incremental(db_session, indexed_fixture):
    """rebuild() and delete_all()+index_blocks(all text ids) yield identical MATCH results."""
    probes = ["horizon", "still", "synthetic", "ZZTOP_TOOLUSE_MARKER"]

    def probe_results():
        return {p: sorted(h.block_id for h in idx.search(db_session, p)[0]) for p in probes}

    idx.rebuild(db_session)
    rebuilt = probe_results()

    all_text_ids = [
        bid
        for (bid,) in db_session.query(ContentBlock.id)
        .filter(ContentBlock.block_kind == "text")
        .all()
    ]
    idx.delete_all(db_session)
    n = idx.index_blocks(db_session, all_text_ids)
    incremental = probe_results()

    assert rebuilt == incremental
    assert n == len(all_text_ids) and n >= 1


@pytest.mark.parametrize(
    "evil",
    ['"unbalanced', "a AND OR", "x NEAR/3 y", "(paren", "*star", "col:on", "-minus", "", "   "],
)
def test_sanitize_never_raises(db_session, indexed_fixture, evil):
    hits, total = idx.search(db_session, evil)  # must not raise
    assert isinstance(hits, list) and isinstance(total, int)


def test_porter_stemming_matches_inflected_forms(db_session, indexed_fixture):
    """Regression guard for porter tokenization: "mapping" must stem-match "maps hours".

    Would catch a silent tokenizer change in content_fts (porter dropped => no stemming)
    or a sanitizer regression that breaks bare-term matching.
    """
    hits, total = idx.search(db_session, "mapping")
    assert total >= 1
    assert any("maps hours" in _strip_marks(h.snippet).lower() for h in hits)


def test_quoted_phrase_matches_exactly(db_session, indexed_fixture):
    hits, _ = idx.search(db_session, '"still water"')
    assert hits  # the assistant line "still water runs deep..." must be found
    assert all("still water" in _strip_marks(h.snippet).lower() for h in hits)


# --- sanitize_query unit semantics --------------------------------------------------------


def test_sanitize_empty_and_whitespace_return_empty():
    assert sanitize_query("") == ""
    assert sanitize_query("   ") == ""
    assert sanitize_query('"') == ""  # a lone dangling quote yields no tokens


def test_sanitize_strips_operators_and_and_joins_bare_terms():
    # Operators/keywords are neutralized (quoted), never emitted as FTS5 operators.
    out = sanitize_query("horizon AND *water col:on")
    assert " AND " in out
    assert "*" not in out and ":" not in out
    assert '"horizon"' in out


def test_sanitize_preserves_balanced_quoted_phrase():
    out = sanitize_query('"still water"')
    assert out == '"still water"'


def test_empty_query_returns_no_hits_without_error(db_session, indexed_fixture):
    hits, total = idx.search(db_session, "")
    assert hits == [] and total == 0


# --- Drift guard: search predicate == migration 0002 backfill predicate --------------------


def test_index_predicate_matches_migration_backfill(db_session, indexed_fixture):
    """Behavioral drift guard: the search index and migration 0002 must index the SAME rows.

    Both carry an independent copy of the text-only predicate
    (block_kind='text' AND text_content IS NOT NULL AND text_content<>''). This builds the
    index BOTH ways over one archive (my rebuild vs the migration's frozen ``_BACKFILL_SQL``)
    and asserts identical MATCH results across a probe set — including a tool_use marker plus
    an empty and a NULL text block that only the correct predicate excludes. Compared via
    MATCH, the sole read that reflects the real external-content index state.
    """
    msg = db_session.query(Message).first()
    db_session.add_all(
        [
            ContentBlock(
                message_id=msg.id,
                block_index=990,
                block_kind="tool_use",
                text_content="DRIFTGUARD_NONTEXT",
                tool_name="Bash",
            ),
            ContentBlock(message_id=msg.id, block_index=991, block_kind="text", text_content=""),
            ContentBlock(message_id=msg.id, block_index=992, block_kind="text", text_content=None),
        ]
    )
    db_session.flush()

    backfill_sql = _load_migration_0002()._BACKFILL_SQL
    # Textual lockstep alongside the behavioral check: the module's predicate constant must
    # appear verbatim inside the migration's frozen backfill SQL (both are single-line,
    # single-spaced, so no whitespace normalization is needed today; normalize if that changes).
    assert _TEXT_PREDICATE in backfill_sql
    probes = ["horizon", "still", "synthetic", "DRIFTGUARD_NONTEXT"]

    def matched_rowids():
        out = {}
        for p in probes:
            hits, _ = idx.search(db_session, p)
            out[p] = sorted(h.block_id for h in hits)
        return out

    # Way A: the search module's own rebuild.
    idx.rebuild(db_session)
    via_rebuild = matched_rowids()

    # Way B: the migration's frozen backfill INSERT, from a cleared index.
    idx.delete_all(db_session)
    db_session.execute(text(backfill_sql))
    via_migration = matched_rowids()

    assert via_rebuild == via_migration
    assert via_rebuild["DRIFTGUARD_NONTEXT"] == []  # neither predicate indexes non-text
    assert via_rebuild["horizon"]  # both index the real text block


# --- Phase 4 Task 2: sidebar content search + project filtering (§14.1/§14.2 core) --------
#
# Fixture geography these tests rely on (pinned in conftest):
#   PROJECT_SLUG_1 "-Users-x-proj"  -> sessions 1 (main + subagent) and 2
#   PROJECT_SLUG_2 "-Users-x-proj2" -> session 3
#   "horizon"  : session 1 main only          "cormorant": session 1 subagent only
#   "synthetic": sessions 1 (subagent), 2, 3   (the distinctness + project-scope probe)

# Same evil inputs the search never-raise test uses; every new path must survive them too.
_EVIL_INPUTS = ['"unbalanced', "a AND OR", "x NEAR/3 y", "(paren", "*star", "col:on", "-minus", "", "   "]


def _message_id_for_phrase(db, phrase: str) -> int:
    """Return the message_id of the (single) text block whose text contains ``phrase``."""
    block = (
        db.query(ContentBlock)
        .filter(ContentBlock.block_kind == "text")
        .filter(ContentBlock.text_content.contains(phrase))
        .one()
    )
    return block.message_id


# --- session_uuids_matching ---------------------------------------------------------------


def test_session_uuids_matching_returns_only_matching_sessions(db_session, indexed_fixture):
    result = idx.session_uuids_matching(db_session, "horizon", None)
    assert result == [SESSION_UUID_1]  # "horizon" lives only in session 1's main transcript


def test_session_uuids_matching_is_distinct(db_session, indexed_fixture):
    # "synthetic" matches multiple blocks in EACH of sessions 1/2/3 — the result must
    # carry every session exactly once (DISTINCT), never one row per matching block.
    result = idx.session_uuids_matching(db_session, "synthetic", None)
    assert sorted(result) == sorted([SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3])
    assert len(result) == len(set(result))  # no duplicate session uuids


def test_session_uuids_matching_project_constraint(db_session, indexed_fixture):
    proj1 = idx.session_uuids_matching(db_session, "synthetic", [PROJECT_SLUG_1])
    assert sorted(proj1) == sorted([SESSION_UUID_1, SESSION_UUID_2])  # session 3 excluded
    proj2 = idx.session_uuids_matching(db_session, "synthetic", [PROJECT_SLUG_2])
    assert proj2 == [SESSION_UUID_3]
    both = idx.session_uuids_matching(db_session, "synthetic", [PROJECT_SLUG_1, PROJECT_SLUG_2])
    assert sorted(both) == sorted([SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3])


def test_session_uuids_matching_none_vs_empty_project_list(db_session, indexed_fixture):
    # None == unfiltered (all matches); [] == matches nothing (documented semantic split).
    assert sorted(idx.session_uuids_matching(db_session, "synthetic", None)) == sorted(
        [SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3]
    )
    assert idx.session_uuids_matching(db_session, "synthetic", []) == []


def test_session_uuids_matching_empty_query_returns_empty(db_session, indexed_fixture):
    assert idx.session_uuids_matching(db_session, "", None) == []
    assert idx.session_uuids_matching(db_session, "   ", None) == []


@pytest.mark.parametrize("evil", _EVIL_INPUTS)
def test_session_uuids_matching_never_raises(db_session, indexed_fixture, evil):
    result = idx.session_uuids_matching(db_session, evil, None)  # must not raise
    assert isinstance(result, list)
    # ...on every path, including with a project constraint applied.
    assert isinstance(idx.session_uuids_matching(db_session, evil, [PROJECT_SLUG_1]), list)


# --- best_snippets (BATCHED — one query, per-session bm25-best) ----------------------------


def test_best_snippets_picks_bm25_best_not_first_rowid(db_session, indexed_fixture):
    """A session's snippet must be its bm25-BEST match, not its first-by-rowid match.

    Two "quantum" blocks are attached to a session-1 message: a WEAK long one (inserted
    first, lower rowid) and a STRONG short high-frequency one (inserted second, higher
    rowid). A bare GROUP BY / first-rowid pick would surface ALPHAMARK; correct min-rank
    selection surfaces BETAMARK.
    """
    msg_id = _message_id_for_phrase(db_session, "horizon")  # a session-1 message
    weak = ContentBlock(
        message_id=msg_id,
        block_index=800,
        block_kind="text",
        text_content=(
            "quantum ALPHAMARK amid a great many other unrelated filler words that dilute "
            "the relevance of this single term very heavily indeed and on and on and on"
        ),
    )
    strong = ContentBlock(
        message_id=msg_id,
        block_index=801,
        block_kind="text",
        text_content="quantum BETAMARK quantum",
    )
    db_session.add(weak)
    db_session.flush()
    db_session.add(strong)
    db_session.flush()
    assert weak.id < strong.id  # the weak match is the FIRST rowid — the trap
    idx.index_blocks(db_session, [weak.id, strong.id])

    snippets = idx.best_snippets(db_session, [SESSION_UUID_1], "quantum")
    assert SESSION_UUID_1 in snippets
    body = snippets[SESSION_UUID_1]
    assert "BETAMARK" in body and "ALPHAMARK" not in body
    assert "<mark>" in body  # marks are included


def test_best_snippets_absent_for_no_match_sessions(db_session, indexed_fixture):
    # "horizon" is in session 1 only; session 2 is listed but must be absent from the dict.
    snippets = idx.best_snippets(db_session, [SESSION_UUID_1, SESSION_UUID_2], "horizon")
    assert set(snippets) == {SESSION_UUID_1}
    assert "horizon" in _strip_marks(snippets[SESSION_UUID_1]).lower()


def test_best_snippets_empty_input_runs_zero_queries(db_session, indexed_fixture):
    from unittest.mock import patch

    with patch.object(db_session, "execute", wraps=db_session.execute) as spy:
        result = idx.best_snippets(db_session, [], "horizon")
    assert result == {}
    spy.assert_not_called()  # empty input list -> empty dict, zero DB round trips


def test_best_snippets_empty_query_returns_empty(db_session, indexed_fixture):
    assert idx.best_snippets(db_session, [SESSION_UUID_1], "") == {}


@pytest.mark.parametrize("evil", _EVIL_INPUTS)
def test_best_snippets_never_raises(db_session, indexed_fixture, evil):
    result = idx.best_snippets(db_session, [SESSION_UUID_1], evil)  # must not raise
    assert isinstance(result, dict)


# --- search(project_slugs=...) global-scope filtering -------------------------------------


def test_search_project_slugs_scopes_hits(db_session, indexed_fixture):
    hits, total = idx.search(db_session, "synthetic", project_slugs=[PROJECT_SLUG_2])
    assert total == len(hits) and total >= 1
    assert {h.session_uuid for h in hits} == {SESSION_UUID_3}


def test_search_project_slugs_none_is_unfiltered(db_session, indexed_fixture):
    base_hits, base_total = idx.search(db_session, "synthetic")
    none_hits, none_total = idx.search(db_session, "synthetic", project_slugs=None)
    assert none_total == base_total
    assert {h.block_id for h in none_hits} == {h.block_id for h in base_hits}
    assert {SESSION_UUID_1, SESSION_UUID_2, SESSION_UUID_3} <= {h.session_uuid for h in none_hits}


def test_search_project_slugs_empty_matches_nothing(db_session, indexed_fixture):
    hits, total = idx.search(db_session, "synthetic", project_slugs=[])
    assert hits == [] and total == 0  # [] == matches nothing (count filtered identically)


@pytest.mark.parametrize("evil", _EVIL_INPUTS)
def test_search_project_slugs_never_raises(db_session, indexed_fixture, evil):
    hits, total = idx.search(db_session, evil, project_slugs=[PROJECT_SLUG_1])  # must not raise
    assert isinstance(hits, list) and isinstance(total, int)
