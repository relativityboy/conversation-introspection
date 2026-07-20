import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { GlobalSearchResult } from '../../api/types'
import { displayTitle } from '../../lib/titles'
import { writeProjects } from '../../lib/urlState'
import { HitSnippet } from './HitSnippet'

export interface GlobalSearchTabProps {
  result: GlobalSearchResult
  q: string
  /** The current app-level project filter (Task 9) — carried onto every link this tab builds. */
  projects: string[]
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

// The grouped (scope=global) results view: one block per session — a serif title that links into
// the conversation, its hits as HitSnippets, and a "more in this conversation" link when the
// group was capped (server SearchGroup.has_more).
export function GlobalSearchTab({ result, q, projects }: GlobalSearchTabProps) {
  if (result.groups.length === 0) {
    return (
      <p style={{ color: 'var(--mist)', fontSize: 14, fontFamily: 'var(--serif)' }}>
        No matches for “{q}”.
      </p>
    )
  }

  // The header link deliberately carries NO `q` (clicking a session title returns to the plain
  // conversation, not a stale search-results view) — only `projects=`, when present. The "more"
  // link keeps its existing `?q=` and gains `projects=` alongside it. Both go through
  // `writeProjects` (the same primitive urlState.ts already uses for the filter bar itself) so
  // there's exactly one comma-join implementation in the app, not a re-derived one here. No
  // manual `?` prefix needed — react-router's `Link` normalizes a bare `search` string (adding
  // the `?` itself, or nothing when it's empty).
  const headerSearch = writeProjects(new URLSearchParams(), projects).toString()
  const moreSearch = writeProjects(new URLSearchParams({ q }), projects).toString()

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
          <Link
            to={{ pathname: `/s/${group.session.session_uuid}`, search: headerSearch }}
            style={HEADER_STYLE}
          >
            {displayTitle(group.session)}
          </Link>
          <div>
            {group.hits.map((hit, i) => (
              <HitSnippet
                key={`${hit.transcript_id}:${hit.block_index}:${i}`}
                sessionUuid={group.session.session_uuid}
                hit={hit}
                q={q}
                projects={projects}
              />
            ))}
          </div>
          {group.has_more && (
            <Link
              to={{ pathname: `/s/${group.session.session_uuid}`, search: moreSearch }}
              style={MORE_STYLE}
            >
              more in this conversation →
            </Link>
          )}
        </section>
      ))}
    </div>
  )
}
