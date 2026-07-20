/**
 * Typed fetch wrapper for the introspect API (`server/src/introspect/api`).
 *
 * `apiFetch` centralizes the two shape rules every route shares (see
 * `server/src/introspect/api/errors.py`): non-2xx responses are problem-details JSON
 * (`{status, title, detail}`), and several endpoints (favorites PUT/DELETE) return a bare
 * 204 with no body.
 */

import type {
  GlobalSearchResult,
  ImportRun,
  MessageList,
  Problem,
  ProjectOut,
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
  chat_only?: boolean
}

export function fetchMessages(
  transcriptId: number,
  opts: MessagesOptions = {},
): Promise<MessageList> {
  const qs = buildQuery({
    offset: opts.offset,
    limit: opts.limit,
    around: opts.around,
    // `1` when true, ABSENT (not `0`) when false -- the server default is false, so a false
    // value sends no signal rather than noise (see routes/sessions.py `chat_only: bool = False`).
    chat_only: opts.chat_only ? 1 : undefined,
  })
  return apiFetch<MessageList>(`/transcripts/${transcriptId}/messages${qs}`)
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
