/**
 * Pure `URLSearchParams` helpers for the sidebar's shareable filter state (`?title=`, `?fav=1`).
 * Kept framework-free (no react-router import) so they're trivially unit-testable and reusable
 * from both the debounced title-filter effect and the favorites-chip click handler.
 */

export interface SidebarParams {
  title: string
  fav: boolean
}

/** Reads the sidebar's own params out of a location's search params, defaulting absent values
 * to "no filter" (`title: ''`, `fav: false`). Anything other than the literal `fav=1` reads as
 * `false` rather than throwing, since a malformed/foreign query string should just mean "off". */
export function readSidebarParams(searchParams: URLSearchParams): SidebarParams {
  return {
    title: searchParams.get('title') ?? '',
    fav: searchParams.get('fav') === '1',
  }
}

export interface SidebarParamsUpdate {
  title?: string
  fav?: boolean
}

/**
 * Returns a NEW `URLSearchParams` with only the given sidebar keys changed — every other param
 * (e.g. a future `?q=` on the main pane) passes through untouched, and `prev` itself is never
 * mutated. Falsy values (empty title, `fav: false`) delete the param rather than writing an
 * empty/`"false"` string, so the URL stays clean once a filter is cleared.
 */
export function writeSidebarParams(
  prev: URLSearchParams,
  updates: SidebarParamsUpdate,
): URLSearchParams {
  const next = new URLSearchParams(prev)

  if ('title' in updates) {
    if (updates.title) {
      next.set('title', updates.title)
    } else {
      next.delete('title')
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
