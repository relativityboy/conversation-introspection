/**
 * Pure `URLSearchParams` helpers for the sidebar's shareable filter state (`?filter=`, `?fav=1`).
 * Kept framework-free (no react-router import) so they're trivially unit-testable and reusable
 * from both the debounced content-filter effect and the favorites-chip click handler.
 */

export interface SidebarParams {
  filter: string
  fav: boolean
}

/** Reads the sidebar's own params out of a location's search params, defaulting absent values
 * to "no filter" (`filter: ''`, `fav: false`). Anything other than the literal `fav=1` reads as
 * `false` rather than throwing, since a malformed/foreign query string should just mean "off".
 * Zero-legacy ruling (Donovan, ledger #4): the retired `?title=` key is never read here — a URL
 * built against the old contract lands unfiltered, not silently upgraded. */
export function readSidebarParams(searchParams: URLSearchParams): SidebarParams {
  return {
    filter: searchParams.get('filter') ?? '',
    fav: searchParams.get('fav') === '1',
  }
}

export interface SidebarParamsUpdate {
  filter?: string
  fav?: boolean
}

/**
 * Returns a NEW `URLSearchParams` with only the given sidebar keys changed — every other param
 * (e.g. a future `?q=` on the main pane) passes through untouched, and `prev` itself is never
 * mutated. Falsy values (empty filter, `fav: false`) delete the param rather than writing an
 * empty/`"false"` string, so the URL stays clean once a filter is cleared.
 */
export function writeSidebarParams(
  prev: URLSearchParams,
  updates: SidebarParamsUpdate,
): URLSearchParams {
  const next = new URLSearchParams(prev)

  if ('filter' in updates) {
    if (updates.filter) {
      next.set('filter', updates.filter)
    } else {
      next.delete('filter')
    }
  }

  if ('fav' in updates) {
    if (updates.fav) {
      next.set('fav', '1')
    } else {
      next.delete('fav')
    }
  }

  return next
}

/**
 * Reads the app-level project filter (`?projects=slug1,slug2`) as a slug list. Absent or empty
 * reads as `[]` ("all projects"). Whitespace is trimmed and empty segments dropped, so a stray
 * or trailing comma never becomes a phantom "" slug. Slugs are returned verbatim (never validated
 * against the known-projects list): an unknown/stale slug from a shared URL renders raw as a chip
 * (ledger #9), same falsy-tolerant spirit as `readSidebarParams`.
 */
export function readProjects(searchParams: URLSearchParams): string[] {
  const raw = searchParams.get('projects')
  if (!raw) return []
  return raw
    .split(',')
    .map((slug) => slug.trim())
    .filter((slug) => slug.length > 0)
}

/**
 * Returns a NEW `URLSearchParams` with `projects` set to the comma-joined slug list — every other
 * param passes through untouched, and `prev` is never mutated. An empty array deletes the param
 * (mirroring `writeSidebarParams`' falsy-deletes idiom) so "all projects" leaves a clean URL.
 */
export function writeProjects(prev: URLSearchParams, slugs: string[]): URLSearchParams {
  const next = new URLSearchParams(prev)
  if (slugs.length > 0) {
    next.set('projects', slugs.join(','))
  } else {
    next.delete('projects')
  }
  return next
}
