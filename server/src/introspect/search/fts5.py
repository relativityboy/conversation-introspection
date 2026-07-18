"""SQLite FTS5 full-text search over ``content_blocks`` (external-content index).

``content_fts`` (created by migration 0002) is an EXTERNAL-CONTENT FTS5 table: it keeps a
shadow inverted index only and reads document text live from ``content_blocks`` by rowid
(``content_blocks.id``). Three consequences shape every method here:

1. **Only non-MATCH-blind reads are trustworthy.** A plain ``SELECT``/``COUNT(*)`` on
   ``content_fts`` is served from ``content_blocks`` and reflects that table's rows, NOT the
   shadow index. Every correctness check goes through a ``MATCH`` query.
2. **The index predicate is text-only and MUST match migration 0002.** ``content_fts``
   indexes exactly ``block_kind='text' AND text_content IS NOT NULL AND text_content<>''``
   (:data:`_TEXT_PREDICATE`). This is an independent copy of migration 0002's frozen
   ``_BACKFILL_SQL`` predicate; ``test_index_predicate_matches_migration_backfill`` asserts
   the two stay equivalent.
3. **Deletes are booby-trapped (the external-content trap).** FTS5 stores no copy of the
   text, so removing a row from the index requires re-supplying the ORIGINAL indexed text
   via the ``'delete'`` command. A bare ``DELETE``/``UPDATE`` against ``content_fts`` — or a
   ``'delete'`` with the wrong text, or for a row that was never indexed — corrupts the
   database file ("database disk image is malformed"; confirmed empirically, see migration
   0002's NOTE). :meth:`delete_for_blocks` therefore re-reads text from the still-present
   ``content_blocks`` rows and its precondition is binding (see its docstring).

This module depends only on the ORM models, SQLAlchemy, and the stdlib — never on
``api``/``ingest``/FastAPI — so search stays a leaf the reader can reuse anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# The text-only index predicate. Frozen copy of migration 0002's ``_BACKFILL_SQL`` WHERE
# clause; kept in lockstep by test_index_predicate_matches_migration_backfill. Only
# non-empty text blocks are searchable — tool_use / thinking / tool_result / NULL are not.
_TEXT_PREDICATE = "block_kind='text' AND text_content IS NOT NULL AND text_content<>''"

# One \w+ run == one FTS5 token. \w (unicode by default for str) excludes every FTS5 operator
# character (" ( ) * : ^ - + and whitespace), so a term built from these runs can never carry
# operator meaning once quoted.
_WORD_RE = re.compile(r"\w+")


@dataclass
class SearchHit:
    """One full-text match, joined back to its conversational location.

    ``block_kind`` is ``'text'`` for every v1 hit (only text blocks are indexed); the field
    exists so a later thinking-searchable index (spec §7) can reuse this shape unchanged.
    ``rank`` is the FTS5 bm25 score — ascending, lower is a better match.
    """

    session_uuid: str
    transcript_id: int
    message_id: int
    record_uuid: str | None
    block_id: int
    block_index: int
    block_kind: str
    snippet: str
    rank: float
    timestamp: datetime | None


class SearchIndex(Protocol):
    """The search surface the reader depends on. FTS5 today; a config point for tsvector later."""

    def index_blocks(self, db: Session, block_ids: list[int]) -> int: ...
    def delete_for_blocks(self, db: Session, block_ids: list[int]) -> int: ...
    def delete_all(self, db: Session) -> None: ...
    def search(
        self,
        db: Session,
        query: str,
        *,
        session_uuid: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SearchHit], int]: ...
    def rebuild(self, db: Session) -> int: ...


def sanitize_query(raw: str) -> str:
    """Turn arbitrary user input into a safe FTS5 MATCH string (empty string == no query).

    FTS5's query grammar makes untrusted input dangerous: a stray ``"``, ``AND``/``OR``/
    ``NEAR``, ``:`` column filter, or ``(`` raises a syntax error mid-search. The rule here
    is *quote everything*: inside a double-quoted phrase FTS5 treats content as literal
    tokens (no operator has meaning), so every emitted token is inert.

    - Balanced ``"..."`` pairs are preserved as phrase tokens (adjacency-matched).
    - Everything else is split into bare terms; each term (and each word of a phrase) is
      reduced to ``\\w+`` runs, dropping all operator characters, then re-quoted.
    - Surviving tokens are AND-joined. Unbalanced quotes degrade to bare terms.

    The output is therefore ALWAYS syntactically valid FTS5 (a set of quoted phrases joined
    by ``AND``) or empty — this is what lets :meth:`Fts5SearchIndex.search` guarantee that
    no input can raise; malformed input simply yields no query and no results.
    """
    if not raw:
        return ""
    # split on '"': an even number of quotes (odd chunk count) means every quote is paired,
    # so odd-indexed chunks are the phrase interiors. An unpaired trailing quote (even chunk
    # count) is dropped — its chunks fall through to the bare-term branch.
    chunks = raw.split('"')
    balanced = len(chunks) % 2 == 1
    tokens: list[str] = []
    for i, chunk in enumerate(chunks):
        if balanced and i % 2 == 1:
            words = _WORD_RE.findall(chunk)
            if words:
                tokens.append('"' + " ".join(words) + '"')
        else:
            tokens.extend('"' + word + '"' for word in _WORD_RE.findall(chunk))
    return " AND ".join(tokens)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse a stored ISO-8601 UTC timestamp back to an aware UTC datetime; never raise."""
    # NOTE(claude): coupled to db.UTCDateTime.process_result_value — that is the source of
    # truth for the stored format; if it changes, change this (raw-SQL reads bypass the ORM).
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# The join from a matched FTS rowid back to its conversational location. ``{session_filter}``
# is empty or an extra ``AND`` on the session — applied identically to search and count so the
# total reflects the same filter as the page.
_SEARCH_FROM = (
    " FROM content_fts"
    " JOIN content_blocks cb ON cb.id = content_fts.rowid"
    " JOIN messages m ON m.id = cb.message_id"
    " JOIN transcripts t ON t.id = m.transcript_id"
    " WHERE content_fts MATCH :match{session_filter}"
)

_SELECT_SQL = (
    "SELECT t.session_id AS session_uuid, m.transcript_id AS transcript_id,"
    " cb.message_id AS message_id, m.record_uuid AS record_uuid, cb.id AS block_id,"
    " cb.block_index AS block_index, cb.block_kind AS block_kind,"
    " snippet(content_fts, 0, '<mark>', '</mark>', '…', 12) AS snippet,"
    " bm25(content_fts) AS rank, m.timestamp AS ts"
    + _SEARCH_FROM
    + " ORDER BY bm25(content_fts) ASC LIMIT :limit OFFSET :offset"
)

_COUNT_SQL = "SELECT COUNT(*)" + _SEARCH_FROM


class Fts5SearchIndex:
    """SQLite FTS5 implementation of :class:`SearchIndex` over ``content_fts``.

    Stateless: all state lives in the DB, so a single shared instance is safe.
    """

    def index_blocks(self, db: Session, block_ids: list[int]) -> int:
        """Index the given blocks, skipping any that are not non-empty text blocks.

        Returns the number of blocks actually added to the index (``block_ids`` outside the
        text-only predicate are silently skipped).
        """
        if not block_ids:
            return 0
        stmt = text(
            "INSERT INTO content_fts(rowid, text_content) "
            "SELECT id, text_content FROM content_blocks "
            f"WHERE id IN :ids AND {_TEXT_PREDICATE}"
        ).bindparams(bindparam("ids", expanding=True))
        return db.execute(stmt, {"ids": block_ids}).rowcount

    def delete_for_blocks(self, db: Session, block_ids: list[int]) -> int:
        """Remove the given blocks from the index. Returns the number actually de-indexed.

        PRECONDITION (binding — the FTS5 external-content trap): callers MUST invoke this
        BEFORE deleting the corresponding ``content_blocks`` rows. FTS5 keeps no copy of the
        text, so de-indexing requires re-supplying the ORIGINAL indexed text; this method
        re-reads it from the still-present rows using the exact index predicate. Issuing a
        ``'delete'`` for a never-indexed row or with mismatched text corrupts the database
        file, so ids outside the predicate (tool_use/thinking/empty/NULL/already-gone) are
        skipped, never deleted.
        """
        if not block_ids:
            return 0
        rows = db.execute(
            text(
                "SELECT id, text_content FROM content_blocks "
                f"WHERE id IN :ids AND {_TEXT_PREDICATE}"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": block_ids},
        ).all()
        # The ONLY safe removal form for an external-content table: the 'delete' command with
        # the original text (never a bare DELETE/UPDATE — see migration 0002's NOTE).
        delete_stmt = text(
            "INSERT INTO content_fts(content_fts, rowid, text_content) "
            "VALUES('delete', :id, :text_content)"
        )
        for row_id, text_content in rows:
            db.execute(delete_stmt, {"id": row_id, "text_content": text_content})
        return len(rows)

    def delete_all(self, db: Session) -> None:
        """Clear the entire index safely via FTS5's ``'delete-all'`` external-content command.

        This is the corruption-safe way to empty an external-content index regardless of
        whether it is in sync with ``content_blocks`` — unlike a bare ``DELETE FROM``.
        """
        db.execute(text("INSERT INTO content_fts(content_fts) VALUES('delete-all')"))

    def search(
        self,
        db: Session,
        query: str,
        *,
        session_uuid: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SearchHit], int]:
        """Full-text search, returning ``(hits, total_match_count)``.

        ``query`` is sanitized (see :func:`sanitize_query`); an empty sanitized query returns
        ``([], 0)`` without touching the DB. Hits are ordered by ascending bm25 rank. The
        second element is the total number of matches for the same filter (not just this
        page). No input can raise: the sanitizer guarantees a syntactically valid MATCH.
        """
        match = sanitize_query(query)
        if not match:
            return [], 0

        session_filter = " AND t.session_id = :session_uuid" if session_uuid is not None else ""
        params: dict[str, object] = {"match": match, "limit": limit, "offset": offset}
        count_params: dict[str, object] = {"match": match}
        if session_uuid is not None:
            params["session_uuid"] = session_uuid
            count_params["session_uuid"] = session_uuid

        rows = db.execute(
            text(_SELECT_SQL.format(session_filter=session_filter)), params
        ).mappings().all()
        hits = [
            SearchHit(
                session_uuid=row["session_uuid"],
                transcript_id=row["transcript_id"],
                message_id=row["message_id"],
                record_uuid=row["record_uuid"],
                block_id=row["block_id"],
                block_index=row["block_index"],
                block_kind=row["block_kind"],
                snippet=row["snippet"],
                rank=float(row["rank"]),
                timestamp=_parse_timestamp(row["ts"]),
            )
            for row in rows
        ]
        total = db.execute(
            text(_COUNT_SQL.format(session_filter=session_filter)), count_params
        ).scalar_one()
        return hits, int(total)

    def rebuild(self, db: Session) -> int:
        """Clear and re-index every text block from scratch. Returns the number indexed.

        Uses :meth:`delete_all` (safe) plus the exact text-only predicate — deliberately NOT
        FTS5's native ``'rebuild'``, which would index every ``content_blocks`` row and
        diverge from the text-only index this module defines.
        """
        self.delete_all(db)
        result = db.execute(
            text(
                "INSERT INTO content_fts(rowid, text_content) "
                "SELECT id, text_content FROM content_blocks "
                f"WHERE {_TEXT_PREDICATE}"
            )
        )
        return result.rowcount


_SEARCH_INDEX = Fts5SearchIndex()


def get_search_index() -> SearchIndex:
    """Return the process-wide search index (the FTS5 implementation).

    A single indirection point so a future tsvector/Postgres backend can be swapped in
    without touching callers.
    """
    return _SEARCH_INDEX
