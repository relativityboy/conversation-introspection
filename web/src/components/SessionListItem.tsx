import type { CSSProperties } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useFavorite } from '../api/hooks'
import type { SessionSummary } from '../api/types'
import { projectDisplayName } from '../lib/projectName'
import { renderSnippet } from '../lib/snippet'
import { displayTitle } from '../lib/titles'
import { HorizonBand } from './HorizonBand'

export interface SessionListItemProps {
  session: SessionSummary
  /** Current sidebar query string (e.g. "?filter=foo&fav=1", or ""), preserved when navigating
   * into the session so returning to the list lands back on the same filter. */
  search: string
  /** True when rendered under its project row in the tree view — the eyebrow would repeat the
   * parent, so it's suppressed. Defaults to false (flat list, eyebrow shown). */
  inTree?: boolean
}

// Styling is inline (not the mockup's CSS classes) — same call as HorizonBand (Task 3): the
// active/favorite states are computed per-item and must be inline anyway, so keeping the static
// bits inline too avoids a new stylesheet. classNames are retained as semantic hooks mirroring
// the mockup vocabulary (.convo-item, .star, .star-on, …), not as styling authorities.
const LINK_STYLE: CSSProperties = {
  display: 'block',
  textDecoration: 'none',
  padding: '11px 12px 12px',
  borderRadius: 8,
  borderLeft: '2px solid transparent',
  cursor: 'pointer',
  color: 'inherit',
}

const LINK_ACTIVE_STYLE: CSSProperties = {
  background: 'var(--shore)',
  borderLeft: '2px solid var(--dragonfly)',
}

// A row segment (title block, meta block). The box padding/border/active state live on the
// container div now, so a segment is just an unstyled block anchor carrying the click target.
const SEGMENT_STYLE: CSSProperties = {
  display: 'block',
  textDecoration: 'none',
  color: 'inherit',
  cursor: 'pointer',
}

// One-line, ellipsized hint shown only when the sidebar's content search matched this session's
// conversation body rather than its title/uuid (`session.match_snippet` non-null — see
// SessionSummary in api/types.ts). `<mark>` spans inside it render via the same shared splitter
// as the search tab (lib/snippet.tsx), so a matched term reads identically in both places.
const SNIPPET_HINT_STYLE: CSSProperties = {
  fontFamily: 'var(--sans)',
  fontSize: 12,
  color: 'var(--mist)',
  marginTop: 4,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

// NOTE(claude): TWO decisions govern this row's structure.
//  1. The snippet hint is its OWN link (→ the matched message) while the title/meta link to the
//     session top. An <a> can't nest inside another <a> (invalid interactive content), so the
//     row is NOT one big anchor: the box (padding, border, active wash) lives on the container
//     div, and title / snippet / meta are SIBLING block anchors inside it. Keeping the snippet
//     visually between title and meta forces this split — title and meta are two separate
//     same-target anchors rather than one, the honest cost of a mid-row second link.
//  2. The star is a SIBLING of the box, absolutely positioned over its top-right corner — never
//     a child (an <a> must not contain a <button>). The title row reserves right padding so text
//     never runs under the overlaid star.
// Active state moved off NavLink (the box isn't an anchor) onto a useLocation match that mirrors
// NavLink's default end=false: active for this session's top AND any descendant (/m/, /a/…).
export function SessionListItem({ session, search, inTree = false }: SessionListItemProps) {
  const favoriteMutation = useFavorite()
  const { pathname } = useLocation()

  const title = displayTitle(session)
  const dateLabel = formatDate(session.last_activity_at)
  const metaText = dateLabel
    ? `${dateLabel} · ${session.message_count} msgs`
    : `${session.message_count} msgs`

  const sessionTop = { pathname: `/s/${session.session_uuid}`, search }
  const isActive =
    pathname === `/s/${session.session_uuid}` ||
    pathname.startsWith(`/s/${session.session_uuid}/`)

  // The snippet deep-links to WHERE it matched: a subagent hit routes through the /a/{hex}/
  // drill-in (mirrors HitSnippet — linking a subagent hit to the main path 404s), a main hit to
  // /s/{uuid}/m/{record}. match_record_uuid is server-guaranteed present whenever match_snippet
  // is; a snippet lacking one (contract violation OR old-server key-absent skew — the walk
  // caught a live /m/undefined against a stale server) degrades to plain text. Loose != null
  // deliberately: it must catch undefined too.
  const snippetPath =
    session.match_record_uuid != null
      ? session.match_agent_hex_id
        ? `/s/${session.session_uuid}/a/${session.match_agent_hex_id}/m/${session.match_record_uuid}`
        : `/s/${session.session_uuid}/m/${session.match_record_uuid}`
      : null

  return (
    <div className="convo-item-wrap" style={{ position: 'relative', marginBottom: 4 }}>
      <div
        className={`convo-item${isActive ? ' active' : ''}`}
        style={{ ...LINK_STYLE, ...(isActive ? LINK_ACTIVE_STYLE : null) }}
      >
        <Link to={sessionTop} style={SEGMENT_STYLE}>
          <div
            style={{
              fontFamily: 'var(--sans)',
              fontSize: 14,
              color: 'var(--moonpaper)',
              lineHeight: 1.3,
              paddingRight: 24,
            }}
          >
            {title}
          </div>
        </Link>
        {session.match_snippet !== null &&
          (snippetPath !== null ? (
            <Link
              to={{ pathname: snippetPath, search }}
              className="convo-snippet-hint"
              style={{ ...SNIPPET_HINT_STYLE, display: 'block', textDecoration: 'none' }}
            >
              {renderSnippet(session.match_snippet)}
            </Link>
          ) : (
            <div className="convo-snippet-hint" style={SNIPPET_HINT_STYLE}>
              {renderSnippet(session.match_snippet)}
            </div>
          ))}
        <Link to={sessionTop} style={SEGMENT_STYLE}>
          {!inTree && (
            <div
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 10,
                color: 'var(--mist)',
                marginTop: 5,
              }}
            >
              {projectDisplayName(session.project_slug)}
            </div>
          )}
          <div
            style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--mist)', marginTop: 3 }}
          >
            {metaText}
          </div>
          <HorizonBand start={session.started_at} end={session.last_activity_at} variant="micro" />
        </Link>
      </div>
      <button
        type="button"
        className={session.favorite ? 'star star-on' : 'star'}
        aria-label="Favorite"
        aria-pressed={session.favorite}
        onClick={() =>
          favoriteMutation.mutate({ uuid: session.session_uuid, favorite: !session.favorite })
        }
        style={{
          position: 'absolute',
          top: 11,
          right: 12,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          fontSize: 15,
          lineHeight: 1,
          padding: '0 2px',
          color: session.favorite ? 'var(--dawn)' : 'var(--mist)',
        }}
      >
        {session.favorite ? '★' : '☆'}
      </button>
    </div>
  )
}

/** "today" / "yesterday" / short local date (e.g. "Jul 11"), compared by LOCAL calendar day
 * (not a rolling 24h/48h window) so a session from 11pm yesterday still reads "yesterday" this
 * morning. */
function formatDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDiff = Math.round((startOfToday.getTime() - startOfDate.getTime()) / 86_400_000)

  if (dayDiff === 0) return 'today'
  if (dayDiff === 1) return 'yesterday'
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
