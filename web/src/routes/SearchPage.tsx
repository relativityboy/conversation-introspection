import type { CSSProperties, FormEvent } from 'react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSearch } from '../api/hooks'
import type { GlobalSearchResult } from '../api/types'
import { GlobalSearchTab } from '../components/search/GlobalSearchTab'
import { readProjects } from '../lib/urlState'

const WRAP_STYLE: CSSProperties = { padding: '18px 24px 40px', maxWidth: 820 }

const INPUT_STYLE: CSSProperties = {
  width: '100%',
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  color: 'var(--moonpaper)',
  fontFamily: 'var(--sans)',
  fontSize: 15,
  padding: '10px 12px',
  borderRadius: 8,
}

const CALM_STYLE: CSSProperties = {
  color: 'var(--mist)',
  fontFamily: 'var(--serif)',
  fontSize: 16,
  marginTop: 22,
}

// The global search surface (/search?q=). The URL's `?q=` is the single source of truth for what
// is searched and displayed; the input is local draft state that only commits to the URL on Enter.
// useSearch itself gates on a non-empty q, so an empty box never touches the API.
export function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const q = searchParams.get('q') ?? ''

  // The box is local draft state, but it must re-sync when q changes from OUTSIDE (back/forward,
  // a tab click that clears it). React's "adjust state during render when a prop changes" pattern
  // (https://react.dev/learn/you-might-not-need-an-effect) — not an effect — keeps it in step
  // without a cascading render; q only changes on deliberate navigation, so live typing is safe.
  const [draft, setDraft] = useState(q)
  const [syncedQ, setSyncedQ] = useState(q)
  if (q !== syncedQ) {
    setSyncedQ(q)
    setDraft(q)
  }

  // Read live, at fire time, same as `q` above — a chip add/remove changes the URL and this
  // re-renders, so the next search fires with the current filter (Task 9).
  const projects = readProjects(searchParams)
  const query = useSearch(q, 'global', undefined, projects.length > 0 ? projects : undefined)

  function commit(event: FormEvent) {
    event.preventDefault()
    const term = draft.trim()
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (term) next.set('q', term)
        else next.delete('q')
        return next
      },
      // Push (not replace): each committed search is its own history entry.
      { replace: false },
    )
  }

  const result = query.data as GlobalSearchResult | undefined

  return (
    <div style={WRAP_STYLE}>
      <form onSubmit={commit} role="search">
        <input
          type="search"
          aria-label="Search all conversations"
          placeholder="Search every archived conversation…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          style={INPUT_STYLE}
        />
      </form>

      {q.trim().length === 0 && <p style={CALM_STYLE}>Search every archived conversation</p>}

      {q.trim().length > 0 && (
        <div style={{ marginTop: 20 }}>
          {query.isPending && <p style={{ color: 'var(--mist)', fontSize: 13 }}>…</p>}
          {query.isError && <p style={{ color: 'var(--mist)', fontSize: 13 }}>archive offline</p>}
          {result && <GlobalSearchTab result={result} q={q} projects={projects} />}
        </div>
      )}
    </div>
  )
}
