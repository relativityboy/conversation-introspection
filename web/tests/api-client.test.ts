import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, fetchSessions, putFavorite } from '../src/api/client'
import { useSearch } from '../src/api/hooks'

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
    await fetchSessions({ favorite: true, title: 'foo bar', limit: 10 })

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    const url = new URL(calledUrl, 'http://localhost')

    expect(url.pathname).toBe('/api/v1/sessions')
    expect(url.searchParams.get('favorite')).toBe('true')
    expect(url.searchParams.get('title')).toBe('foo bar')
    expect(url.searchParams.get('limit')).toBe('10')
    expect(url.searchParams.has('project')).toBe(false)
    expect(url.searchParams.has('offset')).toBe(false)
  })

  it('produces a bare path with no query string when no filters are given', async () => {
    await fetchSessions()

    const calledUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(calledUrl).toBe('/api/v1/sessions')
  })
})

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: queryClient }, children)
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
