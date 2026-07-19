import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useSessions } from '../api/hooks'
import { readSidebarParams, writeSidebarParams } from '../lib/urlState'
import { SessionListItem } from './SessionListItem'

const DEBOUNCE_MS = 250
const SKELETON_ROWS = 3

// Styling is inline, mirroring the mockup vocabulary via className hooks only — same
// convention as HorizonBand (Task 3) and SessionListItem: no new sidebar stylesheet.
const MIST_TEXT: CSSProperties = { color: 'var(--mist)', padding: '10px 6px', fontSize: 13 }

export function Sidebar() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { title: urlTitle, fav } = readSidebarParams(searchParams)

  // The input's visible value is local state, synced to the URL and to the sessions query only
  // after DEBOUNCE_MS of no typing (below). Both are seeded from the URL so a reload/deep-link
  // restores the filter immediately, with no debounce wait on mount.
  const [titleInput, setTitleInput] = useState(urlTitle)
  const [debouncedTitle, setDebouncedTitle] = useState(urlTitle)

  // Guards the debounced URL write. `setSearchParams` is referentially UNSTABLE (react-router
  // hands out a new function whenever the URL changes), so it can't be trusted as an inert dep:
  // without this guard the effect re-runs after its own write (and after every fav-chip click)
  // and writes the same title AGAIN 250ms later. Tracking the last title actually written (seeded
  // with the mount-time URL value) collapses that echo — and the redundant write-on-mount — to
  // exactly one write per settled input.
  const lastWrittenTitle = useRef(urlTitle)

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedTitle(titleInput)
      if (titleInput !== lastWrittenTitle.current) {
        lastWrittenTitle.current = titleInput
        setSearchParams((prev) => writeSidebarParams(prev, { title: titleInput }), {
          replace: true,
        })
      }
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [titleInput, setSearchParams])

  function setFavorite(value: boolean) {
    setSearchParams((prev) => writeSidebarParams(prev, { fav: value }), { replace: true })
  }

  const { data, isLoading, isError, isSuccess } = useSessions({
    title: debouncedTitle || undefined,
    favorite: fav || undefined,
  })

  const search = searchParams.toString()
  const hasFilter = debouncedTitle.length > 0 || fav

  return (
    <>
      <Link
        to="/"
        style={{
          display: 'block',
          fontFamily: 'var(--serif)',
          fontStyle: 'italic',
          fontSize: 15,
          color: 'var(--moonpaper)',
          textDecoration: 'none',
          padding: '2px 6px 14px',
          letterSpacing: '.01em',
        }}
      >
        conversation-introspection
      </Link>

      <input
        type="search"
        placeholder="Filter by title…"
        aria-label="Filter conversations by title"
        value={titleInput}
        onChange={(event) => setTitleInput(event.target.value)}
        style={{
          width: '100%',
          background: 'var(--surface)',
          border: '1px solid var(--shore)',
          color: 'var(--moonpaper)',
          fontFamily: 'var(--sans)',
          fontSize: 13,
          padding: '8px 10px',
          borderRadius: 6,
        }}
      />

      <div
        role="group"
        aria-label="List filter"
        style={{ display: 'flex', gap: 8, margin: '12px 6px 8px' }}
      >
        <button
          type="button"
          aria-pressed={!fav}
          onClick={() => setFavorite(false)}
          style={chipStyle(!fav)}
        >
          All
        </button>
        <button
          type="button"
          aria-pressed={fav}
          onClick={() => setFavorite(true)}
          style={chipStyle(fav)}
        >
          ★ Favorites
        </button>
      </div>

      {/* A plain div, not a nested <nav> — the app shell's own <nav aria-label="Conversation
          archive"> (App.tsx) is already the landmark for this whole region; a second nested
          nav here would create an ambiguous/duplicate "navigation" landmark. */}
      <div className="convo-list" style={{ marginTop: 6 }}>
        {isLoading && <SkeletonRows />}
        {isError && <p style={MIST_TEXT}>archive offline</p>}
        {isSuccess && data.items.length === 0 && (
          <p style={MIST_TEXT}>
            {hasFilter ? 'No conversations match' : 'Archive is empty — run introspect import'}
          </p>
        )}
        {isSuccess &&
          data.items.map((session) => (
            <SessionListItem
              key={session.session_uuid}
              session={session}
              search={search ? `?${search}` : ''}
            />
          ))}
      </div>
    </>
  )
}

function chipStyle(active: boolean): CSSProperties {
  return {
    fontFamily: 'var(--sans)',
    fontSize: 12,
    color: active ? 'var(--depth)' : 'var(--mist)',
    background: active ? 'var(--dragonfly)' : 'transparent',
    border: `1px solid ${active ? 'var(--dragonfly)' : 'var(--shore)'}`,
    borderRadius: 999,
    padding: '4px 12px',
    cursor: 'pointer',
    fontWeight: active ? 600 : 400,
  }
}

// Static (no animation — Still Water is calm) placeholder rows shown only while the sessions
// query is in flight.
function SkeletonRows() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, i) => (
        <div
          key={i}
          className="skeleton-row"
          aria-hidden="true"
          style={{
            height: 60,
            borderRadius: 8,
            background: 'var(--shore)',
            opacity: 0.4,
            marginBottom: 4,
          }}
        />
      ))}
    </>
  )
}
