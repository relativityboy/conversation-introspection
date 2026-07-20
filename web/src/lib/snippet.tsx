import type { ReactNode } from 'react'

// NOTE(claude): the API snippet is FTS5 output (server .../search/fts5.py) — plain text with
// only literal <mark>/</mark> tokens marking the matched terms; the surrounding text is raw
// message content and may contain ANY characters, including angle brackets (e.g. a code snippet
// containing "<script>"). So we never feed the snippet into a raw-HTML sink. Instead we split the
// string on the literal mark tags and hand the pieces to React as text/elements: React escapes
// every text segment, so "<script>" renders as the four visible characters, never a DOM node.
const MARK_RE = /<mark>([\s\S]*?)<\/mark>/g

/** Splits an FTS5 snippet (raw text sprinkled with literal `<mark>…</mark>` tokens) into React
 * children: matched spans become `<mark>` elements (dawn ink, consistent with the search tab),
 * everything else stays plain escaped text. Shared by the search results list (`HitSnippet`) and
 * the sidebar's content-match hint (`SessionListItem`) so both render matches identically. */
export function renderSnippet(snippet: string): ReactNode[] {
  const parts: ReactNode[] = []
  let lastIndex = 0
  let key = 0
  let match: RegExpExecArray | null
  while ((match = MARK_RE.exec(snippet)) !== null) {
    if (match.index > lastIndex) parts.push(snippet.slice(lastIndex, match.index))
    parts.push(
      <mark
        key={key++}
        className="search-mark"
        style={{ background: 'transparent', color: 'var(--dawn)', fontWeight: 600 }}
      >
        {match[1]}
      </mark>,
    )
    lastIndex = MARK_RE.lastIndex
  }
  // exec() leaves lastIndex stateful across calls; reset so the shared regex is reusable.
  MARK_RE.lastIndex = 0
  if (lastIndex < snippet.length) parts.push(snippet.slice(lastIndex))
  return parts
}
