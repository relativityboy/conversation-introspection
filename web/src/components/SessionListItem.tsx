import type { CSSProperties } from 'react'
import { NavLink } from 'react-router-dom'
import { useFavorite } from '../api/hooks'
import type { SessionSummary } from '../api/types'
import { renderSnippet } from '../lib/snippet'
import { displayTitle } from '../lib/titles'
import { HorizonBand } from './HorizonBand'

export interface SessionListItemProps {
  session: SessionSummary
  /** Current sidebar query string (e.g. "?filter=foo&fav=1", or ""), preserved when navigating
   * into the session so returning to the list lands back on the same filter. */
  search: string
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

// NOTE(claude): the star is a SIBLING of the NavLink, absolutely positioned over its top-right
// corner — NOT a child. An <a> must not contain a <button> (invalid interactive-inside-
// interactive content model), and the sibling structure is also what guarantees a star click
// can't navigate: the click never enters the anchor's subtree, so no preventDefault/
// stopPropagation gymnastics are needed. The link's title row reserves right padding so text
// never runs under the overlaid star.
export function SessionListItem({ session, search }: SessionListItemProps) {
  const favoriteMutation = useFavorite()

  const title = displayTitle(session)
  const dateLabel = formatDate(session.last_activity_at)
  const metaText = dateLabel
    ? `${dateLabel} · ${session.message_count} msgs`
    : `${session.message_count} msgs`

  return (
    <div className="convo-item-wrap" style={{ position: 'relative', marginBottom: 4 }}>
      <NavLink
        to={{ pathname: `/s/${session.session_uuid}`, search }}
        className={({ isActive }) => `convo-item${isActive ? ' active' : ''}`}
        style={({ isActive }) => ({ ...LINK_STYLE, ...(isActive ? LINK_ACTIVE_STYLE : null) })}
      >
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
        {session.match_snippet !== null && (
          <div className="convo-snippet-hint" style={SNIPPET_HINT_STYLE}>
            {renderSnippet(session.match_snippet)}
          </div>
        )}
        <div
          style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--mist)', marginTop: 5 }}
        >
          {projectEyebrow(session.project_slug)}
        </div>
        <div
          style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--mist)', marginTop: 3 }}
        >
          {metaText}
        </div>
        <HorizonBand start={session.started_at} end={session.last_activity_at} variant="micro" />
      </NavLink>
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

/** `project_slug` is the CLI's raw source-directory name, e.g. "-Users-x-proj" (see
 * server/src/introspect/ingest/discovery.py). The eyebrow is the tail after the last
 * "-Users-" — deliberately not a full path reconstruction (that would need to guess back the
 * original "/" and "@" characters the CLI collapsed into dashes, which isn't reliably
 * reversible); this is the simple, robust cut. */
function projectEyebrow(slug: string): string {
  const marker = '-Users-'
  const idx = slug.lastIndexOf(marker)
  return idx === -1 ? slug : slug.slice(idx + marker.length)
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
