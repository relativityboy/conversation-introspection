import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { Sidebar } from '../src/components/Sidebar'
import type { SessionSummary } from '../src/api/types'

// Mocking the api client module (not global fetch) per the task contract — hooks.ts imports
// these named functions directly, so replacing the module swaps out every hook's network layer
// in one place. ApiError is a real class instance (imported via importActual) since it's used
// below to construct a realistic error-path fixture.
const { fetchSessions, putFavorite, deleteFavorite } = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  putFavorite: vi.fn(),
  deleteFavorite: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSessions, putFavorite, deleteFavorite }
})

// writeSidebarParams is spied (real implementation preserved) so the debounce test can count
// URL WRITES, not just queries: react-router's setSearchParams is referentially unstable, and
// the echo regression it caused (a second identical write ~250ms after the first) is invisible
// to a fetch-count assertion because the second write doesn't change the query key.
const writeSidebarParamsSpy = vi.hoisted(() => vi.fn())

vi.mock('../src/lib/urlState', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/urlState')>('../src/lib/urlState')
  writeSidebarParamsSpy.mockImplementation(actual.writeSidebarParams)
  return { ...actual, writeSidebarParams: writeSidebarParamsSpy }
})

const DEBOUNCE_MS = 250

const SESSION_A: SessionSummary = {
  session_uuid: '11111111-1111-1111-1111-111111111111',
  project_slug: '-Users-x-proj',
  ai_title: 'Fixing the horizon band',
  custom_title: null,
  started_at: '2026-07-19T10:00:00Z',
  last_activity_at: '2026-07-19T10:30:00Z',
  message_count: 4,
  favorite: false,
}

function renderSidebar(initialEntries: string[] = ['/']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const locationRef: { current: { pathname: string; search: string } | null } = { current: null }

  function LocationProbe() {
    const location = useLocation()
    locationRef.current = { pathname: location.pathname, search: location.search }
    return null
  }

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Sidebar />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  return { queryClient, locationRef, ...utils }
}

beforeEach(() => {
  fetchSessions.mockReset()
  putFavorite.mockReset()
  deleteFavorite.mockReset()
  // mockClear, NOT mockReset — reset would wipe the real implementation installed by the
  // module-mock factory above.
  writeSidebarParamsSpy.mockClear()
})

describe('title filter debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('queries once and writes the URL exactly once, with the final title, after typing settles', async () => {
    renderSidebar()
    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    fetchSessions.mockClear()
    writeSidebarParamsSpy.mockClear()

    const input = screen.getByPlaceholderText('Filter by title…')
    fireEvent.change(input, { target: { value: 'a' } })
    fireEvent.change(input, { target: { value: 'ab' } })
    fireEvent.change(input, { target: { value: 'abc' } })

    // No query yet — each keystroke should have reset the debounce timer, not fired one.
    expect(fetchSessions).not.toHaveBeenCalled()

    // Advance well past 2x the debounce window: the echo regression (setSearchParams's unstable
    // identity re-triggering the effect after its own write) produced a SECOND identical write
    // at ~500ms, which a single 250ms advance could never observe.
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 4)

    expect(fetchSessions).toHaveBeenCalledTimes(1)
    expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ title: 'abc' }))
    expect(writeSidebarParamsSpy).toHaveBeenCalledTimes(1)
    expect(writeSidebarParamsSpy).toHaveBeenCalledWith(expect.anything(), { title: 'abc' })
  })

  it('restores the title filter from the URL on mount without waiting for the debounce', async () => {
    renderSidebar(['/?title=zzz'])

    const input = screen.getByPlaceholderText('Filter by title…') as HTMLInputElement
    expect(input.value).toBe('zzz')
    await vi.waitFor(() =>
      expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ title: 'zzz' })),
    )
  })
})

describe('favorites chip', () => {
  beforeEach(() => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
  })

  it('sets ?fav=1 (replace) and queries with favorite:true when clicked', async () => {
    const { locationRef } = renderSidebar()
    await screen.findByText('Archive is empty — run introspect import')
    fetchSessions.mockClear()

    fireEvent.click(screen.getByRole('button', { name: '★ Favorites' }))

    await vi.waitFor(() => expect(locationRef.current?.search).toBe('?fav=1'))
    expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ favorite: true }))
  })

  it('clicking All clears fav from the URL and queries without a favorite filter', async () => {
    const { locationRef } = renderSidebar(['/?fav=1'])
    await screen.findByText('No conversations match')
    fetchSessions.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'All' }))

    await vi.waitFor(() => expect(locationRef.current?.search).toBe(''))
    expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ favorite: undefined }))
  })
})

describe('favorite star', () => {
  it('fires the favorite mutation without navigating away from the list', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A], total: 1 })
    putFavorite.mockResolvedValue(undefined)

    const { locationRef } = renderSidebar()
    await screen.findByText(SESSION_A.ai_title as string)
    const pathnameBefore = locationRef.current?.pathname

    fireEvent.click(screen.getByRole('button', { name: 'Favorite' }))

    await vi.waitFor(() => expect(putFavorite).toHaveBeenCalledWith(SESSION_A.session_uuid))
    expect(deleteFavorite).not.toHaveBeenCalled()
    expect(locationRef.current?.pathname).toBe(pathnameBefore)
  })

  it('preserves the current sidebar params on the session link href', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A], total: 1 })
    renderSidebar(['/?title=fix'])
    await screen.findByText(SESSION_A.ai_title as string)

    const link = screen.getByRole('link', { name: new RegExp(SESSION_A.ai_title as string) })
    expect(link.getAttribute('href')).toBe(`/s/${SESSION_A.session_uuid}?title=fix`)
  })
})

describe('loading and error states', () => {
  it('renders three static placeholder rows while the query is in flight', () => {
    fetchSessions.mockReturnValue(new Promise(() => {}))
    const { container } = renderSidebar()
    expect(container.querySelectorAll('.skeleton-row')).toHaveLength(3)
  })

  it('renders an inline offline message when the sessions query fails', async () => {
    fetchSessions.mockRejectedValue(new ApiError(500, 'Internal Server Error', 'boom'))
    renderSidebar()
    expect(await screen.findByText('archive offline')).toBeDefined()
  })
})

describe('empty states', () => {
  it('shows the archive-empty message when there are no sessions and no filters are active', async () => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
    renderSidebar()
    expect(await screen.findByText('Archive is empty — run introspect import')).toBeDefined()
  })

  it('shows the no-matches message when a title filter yields zero results', async () => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
    renderSidebar(['/?title=zzz'])
    expect(await screen.findByText('No conversations match')).toBeDefined()
  })
})
