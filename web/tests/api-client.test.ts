import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  apiFetch,
  fetchMessages,
  fetchSearch,
  fetchSessions,
  putArchive,
  putFavorite,
  putSessionTitle,
} from '../src/api/client'
import {
  useArchiveSession,
  useMessages,
  useProjects,
  useSearch,
  useSessionTitle,
} from '../src/api/hooks'

function mockFetchJson(status: number, body: unknown, statusText = '') {
  const json = JSON.stringify(body)
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => JSON.parse(json),
    text: async () => json,
  }) as unknown as typeof fetch
}

function mockFetchEmpty(status: number, statusText = '') {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => {
      throw new Error('json() should never be called on an empty body')
    },
    text: async () => '',
  }) as unknown as typeof fetch
}

describe('apiFetch', () => {
  it('throws ApiError carrying the Problem fields on a 404', async () => {
    mockFetchJson(404, { status: 404, title: 'Not Found', detail: 'session x not found' })

    await expect(apiFetch('/sessions/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      title: 'Not Found',
      detail: 'session x not found',
    })
  })

  it('falls back to statusText when the error body is not Problem-shaped', async () => {
    mockFetchJson(500, { oops: true }, 'Internal Server Error')

    await expect(apiFetch('/status')).rejects.toMatchObject({
      status: 500,
      title: 'Internal Server Error',
      detail: 'Internal Server Error',
    })
  })

  it('resolves undefined on a 204 with no body (favorites PUT)', async () => {
    mockFetchEmpty(204, 'No Content')

    await expect(putFavorite('abc-123')).resolves.toBeUndefined()
  })
})

describe('query-string building', () => {
  beforeEach(() => {
    mockFetchJson(200, { items: [], total: 0 })
  })

  it('includes provided filters and omits undefined ones', async () => {
    await fetchSessions({ favorite: true, q: 'foo bar', limit: 10 })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')

    expect(url.pathname).toBe('/api/v1/sessions')
    expect(url.searchParams.get('favorite')).toBe('true')
    expect(url.searchParams.get('q')).toBe('foo bar')
    expect(url.searchParams.get('limit')).toBe('10')
    expect(url.searchParams.has('projects')).toBe(false)
    expect(url.searchParams.has('offset')).toBe(false)
  })

  it('produces a bare path with no query string when no filters are given', async () => {
    await fetchSessions()

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(calledUrl).toBe('/api/v1/sessions')
  })

  it('joins a non-empty projects filter with commas (fetchSessions)', async () => {
    await fetchSessions({ projects: ['proj-a', 'proj-b'] })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')
    expect(url.searchParams.get('projects')).toBe('proj-a,proj-b')
  })

  it('omits the projects param entirely for an empty array (fetchSessions)', async () => {
    await fetchSessions({ projects: [] })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(calledUrl).toBe('/api/v1/sessions')
  })

  it('joins a non-empty projects filter with commas (fetchSearch, global scope)', async () => {
    await fetchSearch('foo', 'global', undefined, undefined, undefined, ['proj-a', 'proj-b'])

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')
    expect(url.searchParams.get('projects')).toBe('proj-a,proj-b')
  })

  it('omits the projects param entirely for an empty array (fetchSearch)', async () => {
    await fetchSearch('foo', 'global', undefined, undefined, undefined, [])

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')
    expect(url.searchParams.has('projects')).toBe(false)
  })

  it('serializes chat_only as "1" when true (fetchMessages)', async () => {
    await fetchMessages(42, { chat_only: true })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')
    expect(url.searchParams.get('chat_only')).toBe('1')
  })

  it('omits chat_only entirely when false, not "0" (fetchMessages)', async () => {
    await fetchMessages(42, { chat_only: false })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(calledUrl).toBe('/api/v1/transcripts/42/messages')
  })

  it('omits chat_only entirely when not provided (fetchMessages)', async () => {
    await fetchMessages(42)

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(calledUrl).toBe('/api/v1/transcripts/42/messages')
  })
})

describe('putSessionTitle', () => {
  it('resolves undefined on the bare 204 and PUTs {title} as a JSON body', async () => {
    mockFetchEmpty(204, 'No Content')

    await expect(putSessionTitle('abc-123', 'New Title')).resolves.toBeUndefined()

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(url).toBe('/api/v1/sessions/abc-123/title')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({ title: 'New Title' })
  })

  it('sends an empty string verbatim -- the documented revert path', async () => {
    mockFetchEmpty(204, 'No Content')

    await putSessionTitle('abc-123', '')

    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(JSON.parse(init.body as string)).toEqual({ title: '' })
  })
})

describe('putArchive', () => {
  it('PUTs to the archive endpoint and resolves undefined on the bare 204', async () => {
    mockFetchEmpty(204, 'No Content')

    await expect(putArchive('abc-123')).resolves.toBeUndefined()

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(url).toBe('/api/v1/sessions/abc-123/archive')
    expect(init.method).toBe('PUT')
  })
})

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: queryClient }, children)
}

// Unlike `wrapper` above (fresh QueryClient per render, fine for isolated single-hook tests),
// this binds to a caller-supplied client -- needed whenever a test must either spy on that exact
// instance (useSessionTitle's invalidation test) or share one client across multiple renderHook
// calls (the useMessages key-distinction test, where dedup/no-dedup IS the thing under test).
function wrapperWithClient(queryClient: QueryClient) {
  return function ({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

describe('useSearch', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
  })

  it('stays disabled (never fetches) for an empty query', () => {
    const { result } = renderHook(() => useSearch('', 'global'), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('stays disabled (never fetches) for a whitespace-only query', () => {
    const { result } = renderHook(() => useSearch('   ', 'global'), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})

describe('useProjects', () => {
  it('fetches the project list', async () => {
    const projects = [{ id: 1, dir_slug: 'proj-a', resolved_cwd: '/x', session_count: 3 }]
    mockFetchJson(200, projects)

    const { result } = renderHook(() => useProjects(), { wrapper })

    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(projects)
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/v1/projects', undefined)
  })
})

describe('useSessionTitle', () => {
  it('invalidates both the sessions prefix (list + detail) and search on success', async () => {
    mockFetchEmpty(204, 'No Content')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useSessionTitle(), {
      wrapper: wrapperWithClient(queryClient),
    })
    result.current.mutate({ uuid: 'abc-123', title: 'New Title' })

    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['search'] })
  })
})

describe('useArchiveSession', () => {
  it('invalidates both the sessions prefix and search on success', async () => {
    mockFetchEmpty(204, 'No Content')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useArchiveSession(), {
      wrapper: wrapperWithClient(queryClient),
    })
    result.current.mutate('abc-123')

    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['search'] })
  })
})

describe('useMessages chat_only key', () => {
  it('gives different chat_only values distinct query keys (each fetches independently)', async () => {
    mockFetchJson(200, { items: [], total: 0, offset: 0 })
    // ONE shared client: if chat_only weren't part of the key, react-query would dedup these two
    // renders into a single in-flight fetch instead of firing both -- the assertion below on
    // call count (2) and on both distinct URLs is what actually proves key distinctness.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const sharedWrapper = wrapperWithClient(queryClient)

    renderHook(() => useMessages(1, { chat_only: true }), { wrapper: sharedWrapper })
    renderHook(() => useMessages(1, { chat_only: false }), { wrapper: sharedWrapper })

    await vi.waitFor(() =>
      expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2),
    )

    const urls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(
      (call) => call[0] as string,
    )
    expect(urls).toContain('/api/v1/transcripts/1/messages?chat_only=1')
    expect(urls).toContain('/api/v1/transcripts/1/messages')
  })
})
