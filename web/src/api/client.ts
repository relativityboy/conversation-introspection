/**
 * Typed fetch wrapper for the introspect API (`server/src/introspect/api`).
 *
 * `apiFetch` centralizes the two shape rules every route shares (see
 * `server/src/introspect/api/errors.py`): non-2xx responses are problem-details JSON
 * (`{status, title, detail}`), and several endpoints (favorites PUT/DELETE) return a bare
 * 204 with no body.
 */

import type { ViewMode } from '../lib/viewMode'
import type {
  GlobalSearchResult,
  ImportRun,
  MessageList,
  Problem,
  ProjectOut,
  ResumeResult,
  SearchScope,
  SessionDetail,
  SessionList,
  SessionSearchResult,
  StatusOut,
  TriggerImportOut,
} from './types'

const BASE_URL = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly title: string
  readonly detail: string

  constructor(status: number, title: string, detail: string) {
    super(`${status} ${title}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.title = title
    this.detail = detail
  }
}

function isProblem(body: unknown): body is Problem {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as Problem).status === 'number' &&
    typeof (body as Problem).title === 'string' &&
    typeof (body as Problem).detail === 'string'
  )
}

/**
 * Fetch `path` (relative to `/api/v1`) and parse the JSON body as `T`.
 *
 * Non-2xx responses throw `ApiError` built from the Problem JSON body (falling back to the
 * response's `statusText` for both `title` and `detail` when the body isn't Problem-shaped).
 * 204s and other empty bodies resolve `undefined` — `res.json()` is never called
 * unconditionally, since an empty body isn't valid JSON.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, init)

  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      body = null
    }
    if (isProblem(body)) {
      throw new ApiError(body.status, body.title, body.detail)
    }
    throw new ApiError(res.status, res.statusText, res.statusText)
  }

  const text = await res.text()
  if (text === '') {
    return undefined as T
  }
  return JSON.parse(text) as T
}

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const usp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      usp.set(key, String(value))
    }
  }
  const qs = usp.toString()
  return qs ? `?${qs}` : ''
}

/**
 * Comma-join a `projects` chip-list filter for the wire. Shared by `fetchSessions` and
 * `fetchSearch` (global scope) so the two can never drift. An absent or empty list means "no
 * filter" server-side (`_parse_projects_param` treats them identically), so we send nothing
 * rather than an empty `projects=` param -- consistent with `buildQuery`'s undefined-omits rule.
 */
function projectsParam(projects: string[] | undefined): string | undefined {
  return projects && projects.length > 0 ? projects.join(',') : undefined
}

// --- sessions -----------------------------------------------------------------------------

export interface SessionFilters {
  q?: string
  favorite?: boolean
  projects?: string[]
  limit?: number
  offset?: number
}

export function fetchSessions(filters: SessionFilters = {}): Promise<SessionList> {
  const qs = buildQuery({
    q: filters.q,
    favorite: filters.favorite,
    projects: projectsParam(filters.projects),
    limit: filters.limit,
    offset: filters.offset,
  })
  return apiFetch<SessionList>(`/sessions${qs}`)
}

export function fetchSession(uuid: string): Promise<SessionDetail> {
  return apiFetch<SessionDetail>(`/sessions/${encodeURIComponent(uuid)}`)
}

// --- messages -----------------------------------------------------------------------------

export interface MessagesOptions {
  offset?: number
  limit?: number
  around?: string
  view?: ViewMode
}

export function fetchMessages(
  transcriptId: number,
  opts: MessagesOptions = {},
): Promise<MessageList> {
  const qs = buildQuery({
    offset: opts.offset,
    limit: opts.limit,
    around: opts.around,
    // Sent verbatim when present -- unlike the retired boolean flag this replaces, `view`'s three
    // states have no "absent means off" reading, and the server's own default ('all') differs
    // from the client's ('chat'), so every reader call site passes it explicitly (see
    // ConversationView's `withView`).
    view: opts.view,
  })
  return apiFetch<MessageList>(`/transcripts/${transcriptId}/messages${qs}`)
}

// --- raw records --------------------------------------------------------------------------

/**
 * `GET /records/{uuid}/raw` → the record's exact stored `raw_line` as TEXT, never JSON-parsed
 * here: the bytes may be malformed JSON and the raw inspector (§15.2) must be able to show them
 * verbatim; the pretty-print is a client-side concern. Non-2xx still throws `ApiError` from the
 * problem body — the same error contract `apiFetch` uses — but the success path returns
 * `res.text()` untouched (byte-faithful) rather than `JSON.parse`ing it.
 */
export async function fetchRawRecord(uuid: string): Promise<string> {
  const res = await fetch(`${BASE_URL}/records/${encodeURIComponent(uuid)}/raw`)
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      body = null
    }
    if (isProblem(body)) {
      throw new ApiError(body.status, body.title, body.detail)
    }
    throw new ApiError(res.status, res.statusText, res.statusText)
  }
  return res.text()
}

// --- search -------------------------------------------------------------------------------

/**
 * Mirrors the route's own `response_model=GlobalSearchResult | SessionSearchResult`
 * (`server/src/introspect/api/routes/search.py`) — which shape comes back depends on
 * `scope`, so callers narrow on the presence of `groups` vs `items`.
 */
export function fetchSearch(
  q: string,
  scope: SearchScope,
  sessionUuid?: string,
  limit?: number,
  offset?: number,
  // Global-scope callers only -- the server explicitly IGNORES `projects=` under
  // `scope=session` (spec critique #7), so session-scope callers must not pass this.
  projects?: string[],
): Promise<GlobalSearchResult | SessionSearchResult> {
  const qs = buildQuery({
    q,
    scope,
    session: sessionUuid,
    projects: projectsParam(projects),
    // The server defaults to sources=chat (the human<->Claude dialogue only — the trim the
    // "mainly for Claude" read path wants). The room is the human-eyes surface: it asks for
    // everything explicitly, preserving full-archive search behavior.
    sources: 'all',
    limit,
    offset,
  })
  return apiFetch<GlobalSearchResult | SessionSearchResult>(`/search${qs}`)
}

// --- favorites ------------------------------------------------------------------------------

export function putFavorite(uuid: string): Promise<undefined> {
  return apiFetch<undefined>(`/sessions/${encodeURIComponent(uuid)}/favorite`, {
    method: 'PUT',
  })
}

export function deleteFavorite(uuid: string): Promise<undefined> {
  return apiFetch<undefined>(`/sessions/${encodeURIComponent(uuid)}/favorite`, {
    method: 'DELETE',
  })
}

// --- titles ---------------------------------------------------------------------------------

/**
 * `PUT /sessions/{uuid}/title`. Resolves `undefined` on the bare 204 (see `apiFetch`).
 *
 * `title: ''` is the documented revert path -- sent verbatim, no client-side trimming or
 * short-circuiting. The server owns the empty-means-delete semantics (see
 * `server/src/introspect/api/routes/titles.py`): a blank/whitespace title deletes the user
 * title row and the session falls back to its archive-derived title.
 */
export function putSessionTitle(uuid: string, title: string): Promise<undefined> {
  return apiFetch<undefined>(`/sessions/${encodeURIComponent(uuid)}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

// --- archive --------------------------------------------------------------------------------

/**
 * `PUT /sessions/{uuid}/archive`. Resolves `undefined` on the bare 204 (see `apiFetch`).
 *
 * Idempotent server-side (a second PUT is still a 204). There is no client-side unarchive: the
 * server hides archived sessions from every read path and restore is CLI-only (`introspect
 * unarchive`), per spec §15.1. The UI navigates away on success -- see `ArchiveButton`.
 */
export function putArchive(uuid: string): Promise<undefined> {
  return apiFetch<undefined>(`/sessions/${encodeURIComponent(uuid)}/archive`, {
    method: 'PUT',
  })
}

// --- resume ---------------------------------------------------------------------------------

export function postResume(uuid: string): Promise<ResumeResult> {
  return apiFetch<ResumeResult>(`/sessions/${encodeURIComponent(uuid)}/resume`, {
    method: 'POST',
  })
}

// --- admin --------------------------------------------------------------------------------

export function fetchStatus(): Promise<StatusOut> {
  return apiFetch<StatusOut>('/status')
}

export function triggerImport(): Promise<TriggerImportOut> {
  return apiFetch<TriggerImportOut>('/import', { method: 'POST' })
}

export function fetchImportRun(id: number): Promise<ImportRun> {
  return apiFetch<ImportRun>(`/import/runs/${id}`)
}

export function fetchProjects(): Promise<ProjectOut[]> {
  return apiFetch<ProjectOut[]>('/projects')
}
