import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { readSidebarParams, writeSidebarParams } from '../lib/urlState'

const DEBOUNCE_MS = 250

/**
 * Topbar content-filter box (spec §4.1, D1). Owns the input's local state, the 250ms debounce,
 * and every `?filter=` write — Sidebar only reads the URL now, it never writes it.
 */
export function TopbarSearch() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { filter: urlFilter } = readSidebarParams(searchParams)

  // The input's visible value is local state, synced to the URL only after DEBOUNCE_MS of no
  // typing (below). It's seeded from the URL so a reload/deep-link restores the filter
  // immediately, with no debounce wait on mount.
  const [filterInput, setFilterInput] = useState(urlFilter)

  // Guards the debounced URL write. `setSearchParams` is referentially UNSTABLE (react-router
  // hands out a new function whenever the URL changes), so it can't be trusted as an inert dep:
  // without this guard the effect re-runs after its own write (and after every fav-chip click)
  // and writes the same filter AGAIN 250ms later. Tracking the last filter actually written
  // (seeded with the mount-time URL value) collapses that echo — and the redundant write-on-mount
  // — to exactly one write per settled input.
  const lastWrittenFilter = useRef(urlFilter)

  useEffect(() => {
    const timer = setTimeout(() => {
      if (filterInput !== lastWrittenFilter.current) {
        lastWrittenFilter.current = filterInput
        setSearchParams((prev) => writeSidebarParams(prev, { filter: filterInput }), {
          replace: true,
        })
      }
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [filterInput, setSearchParams])

  return (
    <input
      className="sw-input"
      type="search"
      placeholder="Filter by title or content…"
      aria-label="Filter conversations by title or content"
      value={filterInput}
      onChange={(event) => setFilterInput(event.target.value)}
      style={{
        width: 276,
        fontFamily: 'var(--sans)',
        fontSize: 13,
        padding: '8px 10px',
        borderRadius: 6,
      }}
    />
  )
}
