"""Full-text search over the archive's conversational content.

The public surface is the :class:`SearchIndex` protocol plus :func:`get_search_index`
(returning the FTS5 implementation) and :func:`sanitize_query` (exposed for tests). See
:mod:`introspect.search.fts5` for the external-content FTS5 mechanics and the delete trap.
"""

from introspect.search.fts5 import (
    BestSnippet,
    Fts5SearchIndex,
    SearchHit,
    SearchIndex,
    get_search_index,
    sanitize_query,
)

__all__ = [
    "BestSnippet",
    "Fts5SearchIndex",
    "SearchHit",
    "SearchIndex",
    "get_search_index",
    "sanitize_query",
]
