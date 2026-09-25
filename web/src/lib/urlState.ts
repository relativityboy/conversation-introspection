/**
 * Pure `URLSearchParams` helpers for the sidebar's shareable filter state (`?filter=`, `?fav=1`).
 * Kept framework-free (no react-router import) so they're trivially unit-testable and reusable
 * from both TopbarSearch's debounced content-filter effect and Sidebar's favorites-chip click
 * handler.
 */

export interface SidebarParams {
  filter: string
  fav: boolean
}

/** Reads the sidebar's own params out of a location's search params, defaulting absent values
 * to "no filter" (`filter: ''`, `fav: false`). Anything other than the literal `fav=1` reads as
 * `false` rather than throwing, since a malformed/foreign query string should just mean "off".
 * Zero-legacy ruling (relativityboy, ledger #4): the retired `?title=` key is never read here — a URL
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

// --- reader restrict-search toggle (Task T10) ----------------------------------------------
//
// `?restrict=1` — session search only carries the current category selection as `select=` when
// this is on (ConversationSearch/ConversationSearchResults). Deliberately never read by the
// global /search page (SearchPage.tsx never calls these), so the toggle can't leak scope.

/** Reads the restrict-search toggle. Anything other than the literal `restrict=1` reads as off —
 * same falsy-tolerant convention as `readSidebarParams`' `fav`. */
export function readRestrict(searchParams: URLSearchParams): boolean {
  return searchParams.get('restrict') === '1'
}

/** Returns a NEW `URLSearchParams` with `restrict` set — `prev` is never mutated. `false` deletes
 * the param (mirroring `writeSidebarParams`' falsy-deletes idiom) so the toggle being off leaves a
 * clean URL. */
export function writeRestrict(prev: URLSearchParams, value: boolean): URLSearchParams {
  const next = new URLSearchParams(prev)
  if (value) {
    next.set('restrict', '1')
  } else {
    next.delete('restrict')
  }
  return next
}

// --- sidebar subagent-session reveal toggle (Task T14) -------------------------------------
//
// `?subagents=1` — the sidebar's session list defaults to hiding subagent-origin sessions
// (`origin=root,empty` sent to `GET /sessions`); this toggle reveals them (the request drops
// `origin=` entirely, per the API contract's "absent = all"). Named distinctly from the global
// search toggle below (`subagent_sessions`) on purpose — the two surfaces are independently
// stateful even though both are mounted on the SAME URL (Sidebar renders on every route): a
// reader revealing subagent sessions in the sidebar should not silently widen an unrelated
// global-search request, and vice versa.

/** Reads the sidebar's subagent-reveal toggle. Anything other than the literal `subagents=1`
 * reads as off (hidden) — same falsy-tolerant convention as `readRestrict`. */
export function readShowSubagents(searchParams: URLSearchParams): boolean {
  return searchParams.get('subagents') === '1'
}

/** Returns a NEW `URLSearchParams` with `subagents` set — `prev` is never mutated. `false`
 * deletes the param (falsy-deletes idiom) so the default (hidden) state leaves a clean URL. */
export function writeShowSubagents(prev: URLSearchParams, value: boolean): URLSearchParams {
  const next = new URLSearchParams(prev)
  if (value) {
    next.set('subagents', '1')
  } else {
    next.delete('subagents')
  }
  return next
}

// --- global search subagent-session toggle (Task T14) --------------------------------------
//
// `?subagent_sessions=1` — mirrors the `/search` endpoint's own `subagent_sessions=` boolean
// (global scope only; see fetchSearch/useSearch) the same way `?select=`/`?view=` mirror the
// reader's wire params (viewMode.ts) — a direct 1:1 name reuse for a param whose ONLY consumer
// is that one request. Off by default (subagent-origin groups excluded), matching the server's
// own default.

/** Reads the global search page's subagent-inclusion toggle. Anything other than the literal
 * `subagent_sessions=1` reads as off — same falsy-tolerant convention as `readRestrict`. */
export function readSubagentSessions(searchParams: URLSearchParams): boolean {
  return searchParams.get('subagent_sessions') === '1'
}

/** Returns a NEW `URLSearchParams` with `subagent_sessions` set — `prev` is never mutated.
 * `false` deletes the param (falsy-deletes idiom) so the default (off) state leaves a clean URL. */
export function writeSubagentSessions(prev: URLSearchParams, value: boolean): URLSearchParams {
  const next = new URLSearchParams(prev)
  if (value) {
    next.set('subagent_sessions', '1')
  } else {
    next.delete('subagent_sessions')
  }
  return next
}
