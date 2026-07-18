"""search favorites

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18 22:00:00.000000

Adds the ``favorites`` table, the ``content_fts`` full-text-search table (backfilled from
any existing ``content_blocks`` rows), and a partial unique index enforcing at most one
``is_primary`` ``source_files`` row per transcript.

Two preflights run BEFORE any of this migration's DDL/DML, in this order, because this
migration runs automatically on every subcommand and on the next cron tick against a
possibly-populated production archive — a cryptic mid-migration failure or a silently
empty search index would look like data loss:

1. FTS5 availability: some SQLite builds omit the FTS5 extension. Probe with a throwaway
   virtual table and raise a clear, actionable error rather than let SQLite's own
   "no such module: fts5" surface mid-migration.
2. Primary-uniqueness invariant: the new partial unique index (step 5) will refuse to
   apply if any transcript already has more than one ``is_primary`` source file. Check
   for that BEFORE attempting any DDL and raise with the offending transcript_ids, so a
   latent data problem fails loudly with a remediation path instead of bricking the
   migration (and therefore import/serve) on a populated archive.

``content_fts`` is populated with ``INSERT ... SELECT`` using the EXACT incremental-
indexing predicate (``block_kind='text' AND text_content IS NOT NULL AND text_content<>''``)
rather than FTS5's native ``content_fts(content_fts, rank) VALUES('rebuild', '')``: a
native rebuild would index every content_blocks row (tool/thinking/NULL included),
diverging from the text-only index this predicate defines.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen snapshot — never import from src/introspect; later search code keeps its own
# copy of this predicate plus a cross-check test asserting the two match.
_BACKFILL_SQL = (
    "INSERT INTO content_fts(rowid, text_content) "
    "SELECT id, text_content FROM content_blocks "
    "WHERE block_kind='text' AND text_content IS NOT NULL AND text_content<>''"
)


def _check_fts5_available(conn) -> None:  # noqa: ANN001
    """Probe for FTS5 support; raise a clear, actionable error if the build lacks it."""
    try:
        conn.exec_driver_sql("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.exec_driver_sql("DROP TABLE _fts5_probe")
    except Exception as exc:
        raise RuntimeError(
            "This SQLite build lacks FTS5 support, so migration 0002 cannot create the "
            "content_fts search table. Rebuild/upgrade sqlite3 with FTS5 enabled "
            "(most modern Python builds include it; check `python -c "
            "'import sqlite3; sqlite3.connect(\":memory:\").execute("
            "\"CREATE VIRTUAL TABLE t USING fts5(x)\")'`) before migrating."
        ) from exc


def find_double_primary_transcripts(conn) -> list[int]:  # noqa: ANN001
    """Return transcript_ids with more than one ``is_primary`` source_files row.

    A non-empty result means the new partial unique index (step 5) would fail to apply —
    the caller must raise before attempting it.
    """
    rows = conn.exec_driver_sql(
        "SELECT transcript_id FROM source_files "
        "WHERE is_primary GROUP BY transcript_id HAVING COUNT(*) > 1"
    ).fetchall()
    return [row[0] for row in rows]


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: FTS5 availability preflight.
    _check_fts5_available(conn)

    # Step 2: primary-invariant preflight (must run before the index in step 5).
    dupes = find_double_primary_transcripts(conn)
    if dupes:
        raise RuntimeError(
            "Cannot enforce one-primary-source-file-per-transcript: transcript_id(s) "
            f"{dupes} each have more than one is_primary=1 source_files row. Fix the "
            "archive data (demote all but one is_primary row per listed transcript) "
            "before re-running this migration."
        )

    op.create_table(
        'favorites',
        sa.Column('session_uuid', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['session_uuid'], ['sessions.session_uuid']),
        sa.PrimaryKeyConstraint('session_uuid'),
    )

    # Step 3: create content_fts (external-content FTS5 table over content_blocks).
    #
    # NOTE(claude): external-content FTS5 tables do NOT store their own copy of
    # text_content — a plain (non-MATCH) `SELECT ... FROM content_fts` (including
    # `COUNT(*)`) is served live from content_blocks by rowid, so it reflects
    # content_blocks' current row count REGARDLESS of what has actually been inserted
    # into the FTS shadow index. Only a MATCH query reflects the real index state.
    # Worse: issuing a bare `DELETE FROM content_fts` (or `UPDATE`/row-level `DELETE`)
    # while the shadow index is OUT OF SYNC with content_blocks (e.g. content_blocks
    # rows exist that were never inserted into content_fts) corrupts the database file
    # ("database disk image is malformed") — confirmed empirically while testing this
    # migration. Whoever adds live sync triggers on content_blocks (a later task) MUST
    # keep content_fts synchronized on every write — on INSERT, insert into content_fts;
    # on UPDATE/DELETE, use `INSERT INTO content_fts(content_fts, rowid, text_content)
    # VALUES('delete', old.id, old.text_content)` with the OLD row's values BEFORE the
    # content_blocks row changes/disappears — never a bare DELETE/UPDATE against
    # content_fts, and never let the two tables drift out of sync.
    conn.exec_driver_sql(
        "CREATE VIRTUAL TABLE content_fts USING fts5("
        "text_content, content='content_blocks', content_rowid='id', "
        "tokenize='porter unicode61')"
    )

    # Step 4: backfill atomically with creation, using the exact incremental predicate.
    conn.exec_driver_sql(_BACKFILL_SQL)

    # Step 5: partial unique index — at most one is_primary source file per transcript.
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_one_primary_per_transcript "
        "ON source_files(transcript_id) WHERE is_primary"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_one_primary_per_transcript")
    op.execute("DROP TABLE content_fts")
    op.drop_table('favorites')
