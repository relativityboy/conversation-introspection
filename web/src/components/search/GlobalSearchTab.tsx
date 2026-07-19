import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { GlobalSearchResult, SessionSummary } from '../../api/types'
import { HitSnippet } from './HitSnippet'

export interface GlobalSearchTabProps {
  result: GlobalSearchResult
  q: string
}

const GROUP_STYLE: CSSProperties = { margin: '0 0 26px' }

const HEADER_STYLE: CSSProperties = {
  display: 'inline-block',
  fontFamily: 'var(--serif)',
  fontSize: 17,
  fontWeight: 600,
  color: 'var(--moonpaper)',
  textDecoration: 'none',
  marginBottom: 6,
}

const MORE_STYLE: CSSProperties = {
  display: 'inline-block',
  fontFamily: 'var(--sans)',
  fontSize: 12,
  color: 'var(--mist)',
  textDecoration: 'none',
  marginTop: 6,
  paddingLeft: 10,
}

function sessionTitle(session: SessionSummary): string {
  // Same fallback chain as SessionListItem / SessionPage.
  return session.ai_title ?? session.custom_title ?? session.session_uuid.slice(0, 8)
}

// The grouped (scope=global) results view: one block per session — a serif title that links into
// the conversation, its hits as HitSnippets, and a "more in this conversation" link when the
// group was capped (server SearchGroup.has_more).
export function GlobalSearchTab({ result, q }: GlobalSearchTabProps) {
  if (result.groups.length === 0) {
    return (
      <p style={{ color: 'var(--mist)', fontSize: 14, fontFamily: 'var(--serif)' }}>
        No matches for “{q}”.
      </p>
    )
  }

  const search = `?q=${encodeURIComponent(q)}`

  return (
    <div>
      <p
        className="mono"
        style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--mist)', margin: '0 0 18px' }}
      >
        {result.total} {result.total === 1 ? 'match' : 'matches'}
      </p>

      {result.groups.map((group) => (
        <section key={group.session.session_uuid} style={GROUP_STYLE}>
          <Link to={`/s/${group.session.session_uuid}`} style={HEADER_STYLE}>
            {sessionTitle(group.session)}
          </Link>
          <div>
            {group.hits.map((hit, i) => (
              <HitSnippet
                key={`${hit.transcript_id}:${hit.block_index}:${i}`}
                sessionUuid={group.session.session_uuid}
                hit={hit}
                q={q}
              />
            ))}
          </div>
          {group.has_more && (
            <Link to={{ pathname: `/s/${group.session.session_uuid}`, search }} style={MORE_STYLE}>
              more in this conversation →
            </Link>
          )}
        </section>
      ))}
    </div>
  )
}
