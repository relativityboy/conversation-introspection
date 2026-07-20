import type { CSSProperties, FormEvent } from 'react'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useSearch } from '../../api/hooks'
import type { SessionSearchResult } from '../../api/types'
import { readProjects } from '../../lib/urlState'
import { HitSnippet } from './HitSnippet'

const INPUT_STYLE: CSSProperties = {
  width: '100%',
  maxWidth: 360,
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  color: 'var(--moonpaper)',
  fontFamily: 'var(--sans)',
  fontSize: 13,
  padding: '7px 10px',
  borderRadius: 6,
}

const BACK_STYLE: CSSProperties = {
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  padding: 0,
  fontFamily: 'var(--sans)',
  fontSize: 13,
  color: 'var(--dragonfly)',
}

/**
 * The conversation-scoped search box that lives in the session header. Committing (Enter) always
 * navigates to the BASE session path `/s/{uuid}?q=term` (a push, and dropping any `/m/` deep-link
 * segment) so a search lands on the results view rather than a stale scrolled-to message.
 */
export function ConversationSearch({ sessionUuid }: { sessionUuid: string }) {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const q = searchParams.get('q') ?? ''

  // Re-sync the box when q changes externally (deep-link click, back/forward) via React's
  // adjust-state-during-render pattern rather than an effect. See SearchPage for the rationale.
  const [draft, setDraft] = useState(q)
  const [syncedQ, setSyncedQ] = useState(q)
  if (q !== syncedQ) {
    setSyncedQ(q)
    setDraft(q)
  }

  function commit(event: FormEvent) {
    event.preventDefault()
    const term = draft.trim()
    const next = new URLSearchParams(searchParams)
    if (term) next.set('q', term)
    else next.delete('q')
    const qs = next.toString()
    navigate({ pathname: `/s/${sessionUuid}`, search: qs ? `?${qs}` : '' })
  }

  return (
    <form onSubmit={commit} role="search">
      <input
        type="search"
        aria-label="Search this conversation"
        placeholder="Search this conversation…"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        style={INPUT_STYLE}
      />
    </form>
  )
}

/**
 * The conversation-scoped results panel, rendered in place of ConversationView while `?q=` is set
 * (and no `/m/` deep link is active). A flat, rank-ordered hit list plus a "back to conversation"
 * affordance that clears `q` (replace — leaving is not its own history entry).
 */
export function ConversationSearchResults({ sessionUuid, q }: { sessionUuid: string; q: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  // Session-scope search never passes projects to the server (see useSearch's call below — the
  // 4th arg is simply omitted, per §14.2: filtering by project within a single already-scoped
  // session is meaningless, and the server explicitly ignores it too). The URL's ?projects=,
  // read here, is used ONLY to carry the filter onto each hit's deep link (a link-preservation
  // concern, not a query one) — see the `projects` prop passed to HitSnippet below.
  const projects = readProjects(searchParams)
  const query = useSearch(q, 'session', sessionUuid)
  const result = query.data as SessionSearchResult | undefined

  function backToConversation() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('q')
        return next
      },
      { replace: true },
    )
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '10px 24px 40px' }}>
      <button type="button" onClick={backToConversation} style={BACK_STYLE}>
        ← back to conversation
      </button>

      <div style={{ marginTop: 14 }}>
        {query.isPending && <p style={{ color: 'var(--mist)', fontSize: 13 }}>…</p>}
        {query.isError && <p style={{ color: 'var(--mist)', fontSize: 13 }}>archive offline</p>}
        {result && result.items.length === 0 && (
          <p style={{ color: 'var(--mist)', fontSize: 14, fontFamily: 'var(--serif)' }}>
            No matches for “{q}” in this conversation.
          </p>
        )}
        {result && result.items.length > 0 && (
          <>
            <p
              className="mono"
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 11,
                color: 'var(--mist)',
                margin: '0 0 12px',
              }}
            >
              {result.total} {result.total === 1 ? 'match' : 'matches'}
            </p>
            {result.items.map((hit, i) => (
              <HitSnippet
                key={`${hit.transcript_id}:${hit.block_index}:${i}`}
                sessionUuid={sessionUuid}
                hit={hit}
                q={q}
                projects={projects}
              />
            ))}
          </>
        )}
      </div>
    </div>
  )
}
