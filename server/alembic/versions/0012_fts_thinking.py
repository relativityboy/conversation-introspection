"""fts thinking

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-20 01:00:00.000000

Widens ``content_fts``'s indexed predicate to also cover non-empty ``thinking`` blocks
(previously text-only, per migration 0002). ``content_blocks.text_content`` for
``block_kind='thinking'`` rows was already populated by ``ingest/interpret.py`` -- the CLI
persists thinking text empty most of the time, but a real minority of rows carry non-empty
text -- so this is a REBUILD of the search index, not a reparse: no raw bytes are touched and
interpretation does not re-run.

This is the ONLY mechanism that converges an already-populated archive: an existing FTS5
external-content index has no ALTER-predicate operation, so the sole way to widen what it
covers is to empty it and re-insert under the new predicate. That happens automatically the
next time this migration runs (on `introspect update`/serve start, no manual owner ritual),
so every archive -- fresh or years old -- ends up with identical thinking coverage without an
operator running a separate rebuild command.

Mirrors migration 0002's mechanism exactly: an explicit FTS5 ``'delete-all'`` command (the
only corruption-safe way to empty an external-content index regardless of sync state -- see
0002's NOTE) followed by an ``INSERT INTO content_fts(rowid, text_content) SELECT ...`` using
the frozen predicate below -- never FTS5's native ``'rebuild'`` command, which is
predicate-free and would index every block kind (tool_use/tool_result included).

``search/fts5.py`` keeps its own independent copy of this predicate (``_INDEXED_PREDICATE``);
``test_index_predicate_matches_migration_backfill`` asserts the two never drift apart. Going
forward, new captures need no extra step either: ``search/fts5.py``'s ``index_blocks`` /
``delete_for_blocks`` already route every block id through this same predicate (they were
never hardcoded to ``block_kind='text'`` specifically), so widening the shared predicate
constant is sufficient for both live ingestion and this one-time convergence.

Migration 0002 itself is applied history and is not edited (CLAUDE.md: zero legacy, no
retroactive rewrites of shipped migrations) -- its frozen ``_BACKFILL_SQL`` stays text-only on
purpose; this migration supersedes it as the current predicate authority.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen snapshot -- never import from src/introspect; search/fts5.py keeps its own copy of
# this predicate (_INDEXED_PREDICATE) plus a cross-check test asserting the two match.
_INDEXED_SQL = (
    "INSERT INTO content_fts(rowid, text_content) "
    "SELECT id, text_content FROM content_blocks "
    "WHERE block_kind IN ('text', 'thinking') AND text_content IS NOT NULL AND text_content<>''"
)

# Migration 0002's original text-only predicate, frozen here (independently of 0002's own
# module) so downgrade can restore exactly what 0002 built -- 0002 itself is never edited.
_TEXT_ONLY_SQL = (
    "INSERT INTO content_fts(rowid, text_content) "
    "SELECT id, text_content FROM content_blocks "
    "WHERE block_kind='text' AND text_content IS NOT NULL AND text_content<>''"
)


def upgrade() -> None:
    conn = op.get_bind()
    # The corruption-safe way to empty an external-content FTS5 index (see 0002's NOTE) --
    # a bare DELETE against content_fts is not safe.
    conn.exec_driver_sql("INSERT INTO content_fts(content_fts) VALUES('delete-all')")
    conn.exec_driver_sql(_INDEXED_SQL)


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("INSERT INTO content_fts(content_fts) VALUES('delete-all')")
    conn.exec_driver_sql(_TEXT_ONLY_SQL)
