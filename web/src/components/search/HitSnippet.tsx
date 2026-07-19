import type { CSSProperties, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { HitOut } from '../../api/types'

export interface HitSnippetProps {
  /** The session this hit belongs to — the deep link needs it (global results supply it from
   * the group; conversation-scoped results supply their own). */
  sessionUuid: string
  hit: HitOut
  /** Current query, carried onto the deep link so the arrival view can offer "back to results". */
  q: string
}

const ROW_STYLE: CSSProperties = {
  display: 'block',
  textDecoration: 'none',
  color: 'inherit',
  padding: '8px 10px',
  borderRadius: 6,
  borderLeft: '2px solid transparent',
}

const BADGE_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 10,
  letterSpacing: '.08em',
  textTransform: 'uppercase',
  color: 'var(--mist)',
  border: '1px solid var(--shore)',
  borderRadius: 4,
  padding: '1px 6px',
}

const SNIPPET_STYLE: CSSProperties = {
  fontFamily: 'var(--serif)',
  fontSize: 15,
  lineHeight: 1.55,
  color: 'var(--moonpaper)',
  margin: '6px 0 0',
}

// NOTE(claude): the API snippet is FTS5 output (server .../search/fts5.py) — plain text with
// only literal <mark>/</mark> tokens marking the matched terms; the surrounding text is raw
// message content and may contain ANY characters, including angle brackets (e.g. a code snippet
// containing "<script>"). So we never feed the snippet into a raw-HTML sink. Instead we split the
// string on the literal mark tags and hand the pieces to React as text/elements: React escapes
// every text segment, so "<script>" renders as the four visible characters, never a DOM node.
const MARK_RE = /<mark>([\s\S]*?)<\/mark>/g

function renderSnippet(snippet: string): ReactNode[] {
  const parts: ReactNode[] = []
  let lastIndex = 0
  let key = 0
  let match: RegExpExecArray | null
  while ((match = MARK_RE.exec(snippet)) !== null) {
    if (match.index > lastIndex) parts.push(snippet.slice(lastIndex, match.index))
    parts.push(
      <mark key={key++} className="search-mark" style={{ background: 'transparent', color: 'var(--dawn)', fontWeight: 600 }}>
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

/** Short local time (e.g. "Jul 11, 14:12"); empty when the timestamp is absent/unparsable. */
function shortLocal(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function HitSnippet({ sessionUuid, hit, q }: HitSnippetProps) {
  const search = `?q=${encodeURIComponent(q)}`
  // record_uuid is nullable in the API contract; a hit without one can only land on the session
  // (there's no message to seed the around-window with), so degrade to the session route.
  const to = hit.record_uuid
    ? { pathname: `/s/${sessionUuid}/m/${hit.record_uuid}`, search }
    : { pathname: `/s/${sessionUuid}`, search }
  const time = shortLocal(hit.timestamp)

  return (
    <Link className="hit-snippet" to={to} style={ROW_STYLE}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="mono" style={BADGE_STYLE}>
          {hit.block_kind}
        </span>
        {time && (
          <span className="mono" style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--mist)' }}>
            {time}
          </span>
        )}
      </div>
      <p style={SNIPPET_STYLE}>{renderSnippet(hit.snippet)}</p>
    </Link>
  )
}
