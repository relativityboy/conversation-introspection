import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { HitOut } from '../../api/types'
import { writeProjects } from '../../lib/urlState'
import { renderSnippet } from '../../lib/snippet'

export interface HitSnippetProps {
  /** The session this hit belongs to — the deep link needs it (global results supply it from
   * the group; conversation-scoped results supply their own). */
  sessionUuid: string
  hit: HitOut
  /** Current query, carried onto the deep link so the arrival view can offer "back to results". */
  q: string
  /** Current app-level project filter (Task 9), carried onto the deep link alongside q. Optional
   * (defaults to none) — this is a link-preservation concern independent of whether the caller's
   * own search query used projects (session-scope search never does; the link still should). */
  projects?: string[]
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

export function HitSnippet({ sessionUuid, hit, q, projects = [] }: HitSnippetProps) {
  const search = `?${writeProjects(new URLSearchParams({ q }), projects).toString()}`
  // A hit in a SUBAGENT transcript must deep-link through the /a/{hex}/ drill-in, not the
  // main-conversation path: linking it to /s/{uuid}/m/{uuid} makes SessionPage fetch the MAIN
  // transcript with a foreign record uuid, which 404s. agent_hex_id is the server's per-hit
  // routing signal (null for main-transcript hits). See server search route (Task P3-10).
  const base = hit.agent_hex_id ? `/s/${sessionUuid}/a/${hit.agent_hex_id}` : `/s/${sessionUuid}`
  // record_uuid is nullable in the API contract; a hit without one can only land on the
  // transcript base (there's no message to seed the around-window with), so degrade to it.
  const to = hit.record_uuid
    ? { pathname: `${base}/m/${hit.record_uuid}`, search }
    : { pathname: base, search }
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
