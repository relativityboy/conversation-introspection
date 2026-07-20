import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { ProjectFilterBar } from '../src/components/ProjectFilterBar'
import { Sidebar } from '../src/components/Sidebar'
import type { SessionSummary } from '../src/api/types'

// Mocking the api client module (not global fetch) per the task contract — hooks.ts imports
// these named functions directly, so replacing the module swaps out every hook's network layer
// in one place. ApiError is a real class instance (imported via importActual) since it's used
// below to construct a realistic error-path fixture. fetchProjects is only exercised by the Task
// 9 integration test that mounts the real ProjectFilterBar alongside Sidebar.
const { fetchSessions, putFavorite, deleteFavorite, fetchProjects } = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  putFavorite: vi.fn(),
  deleteFavorite: vi.fn(),
  fetchProjects: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSessions, putFavorite, deleteFavorite, fetchProjects }
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
  user_title: null,
  started_at: '2026-07-19T10:00:00Z',
  last_activity_at: '2026-07-19T10:30:00Z',
  message_count: 4,
  favorite: false,
  match_snippet: null,
}

const SESSION_B: SessionSummary = {
  session_uuid: '22222222-2222-2222-2222-222222222222',
  project_slug: '-Users-x-proj',
  ai_title: 'Refactoring the tide pool',
  custom_title: null,
  user_title: null,
  started_at: '2026-07-18T09:00:00Z',
  last_activity_at: '2026-07-18T09:20:00Z',
  message_count: 2,
  favorite: false,
  match_snippet: 'a <mark>tidal</mark> wave of changes',
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
  fetchProjects.mockReset()
  // mockClear, NOT mockReset — reset would wipe the real implementation installed by the
  // module-mock factory above.
  writeSidebarParamsSpy.mockClear()
})

describe('content filter debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('queries once and writes the URL exactly once, with the final filter, after typing settles', async () => {
    renderSidebar()
    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    fetchSessions.mockClear()
    writeSidebarParamsSpy.mockClear()

    const input = screen.getByPlaceholderText('Filter by title or content…')
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
    // fetchSessions receives SessionFilters.q (the server-facing filter param, searching title
    // OR content OR uuid) -- the URL param the input syncs to is a separate contract (`filter`,
    // see urlState.ts and the writeSidebarParamsSpy assertion below).
    expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ q: 'abc' }))
    expect(writeSidebarParamsSpy).toHaveBeenCalledTimes(1)
    expect(writeSidebarParamsSpy).toHaveBeenCalledWith(expect.anything(), { filter: 'abc' })
  })

  it('restores the content filter from the URL on mount without waiting for the debounce', async () => {
    renderSidebar(['/?filter=zzz'])

    const input = screen.getByPlaceholderText('Filter by title or content…') as HTMLInputElement
    expect(input.value).toBe('zzz')
    await vi.waitFor(() =>
      expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ q: 'zzz' })),
    )
  })

  // Zero-legacy ruling (Donovan, ledger #4): the retired `?title=` key must NOT seed the input.
  // A deep link built against the old contract lands on an unfiltered list, not a silently
  // resurrected filter.
  it('does not seed the input from the retired `?title=` param', async () => {
    renderSidebar(['/?title=zzz'])

    const input = screen.getByPlaceholderText('Filter by title or content…') as HTMLInputElement
    expect(input.value).toBe('')
    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ q: undefined }))
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
    renderSidebar(['/?filter=fix'])
    await screen.findByText(SESSION_A.ai_title as string)

    const link = screen.getByRole('link', { name: new RegExp(SESSION_A.ai_title as string) })
    expect(link.getAttribute('href')).toBe(`/s/${SESSION_A.session_uuid}?filter=fix`)
  })

  // Task 9: SessionListItem itself needs no code change for this — Sidebar already passes the
  // FULL current query string as the `search` prop — but it's the one deep link every session
  // click goes through, so it earns an explicit regression test against the new param.
  it('preserves ?projects= on the session link href', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A], total: 1 })
    renderSidebar(['/?projects=alpha,mid'])
    await screen.findByText(SESSION_A.ai_title as string)

    const link = screen.getByRole('link', { name: new RegExp(SESSION_A.ai_title as string) })
    // %2C, not a literal comma: `search` is `searchParams.toString()`, and URLSearchParams
    // percent-encodes commas on serialization (confirmed against Node's URLSearchParams directly
    // — not a Task 9 regression). readProjects().get() decodes it straight back, so the filter
    // itself round-trips correctly; only the raw href text is affected.
    expect(link.getAttribute('href')).toBe(`/s/${SESSION_A.session_uuid}?projects=alpha%2Cmid`)
  })
})

describe('project filter plumbing (Task 9)', () => {
  beforeEach(() => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
  })

  it('threads ?projects= from the URL into useSessions', async () => {
    renderSidebar(['/?projects=alpha,mid'])
    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    expect(fetchSessions).toHaveBeenCalledWith(
      expect.objectContaining({ projects: ['alpha', 'mid'] }),
    )
  })

  it('omits the projects key entirely when the URL has no ?projects=', async () => {
    renderSidebar()
    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    const call = fetchSessions.mock.calls[0][0]
    expect(Object.prototype.hasOwnProperty.call(call, 'projects')).toBe(false)
  })

  // Integration with ProjectFilterBar (Task 8): removing the last chip writes the URL back to
  // having no ?projects= at all, and the sidebar's live useSessions key must follow — this is the
  // "removing the last chip -> unfiltered everything" contract clause (§14.2).
  it('re-queries unfiltered when the last project chip is removed via ProjectFilterBar', async () => {
    fetchProjects.mockResolvedValue([])
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/?projects=alpha']}>
          <ProjectFilterBar />
          <Sidebar />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await vi.waitFor(() =>
      expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ projects: ['alpha'] })),
    )
    fetchSessions.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Remove alpha' }))

    await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
    const call = fetchSessions.mock.calls[0][0]
    expect(Object.prototype.hasOwnProperty.call(call, 'projects')).toBe(false)
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

  it('shows the no-matches message when a content filter yields zero results', async () => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
    renderSidebar(['/?filter=zzz'])
    expect(await screen.findByText('No conversations match')).toBeDefined()
  })
})

describe('content-match snippet hint', () => {
  it('renders a mist hint with a highlighted mark for a session carrying match_snippet', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A, SESSION_B], total: 2 })
    const { container } = renderSidebar()
    await screen.findByText(SESSION_B.ai_title as string)

    const marks = container.querySelectorAll('mark')
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('tidal')
    expect(container.querySelector('.convo-snippet-hint')?.textContent).toBe(
      'a tidal wave of changes',
    )
  })

  it('renders no hint node for a session with no content match', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A], total: 1 })
    const { container } = renderSidebar()
    await screen.findByText(SESSION_A.ai_title as string)

    expect(container.querySelector('.convo-snippet-hint')).toBeNull()
    expect(container.querySelectorAll('mark')).toHaveLength(0)
  })
})

// --- title precedence (§14.3 binding, enforced identically at every render site):
// user_title > ai_title > custom_title > uuid-prefix ------------------------------------------

describe('title precedence', () => {
  it('shows user_title over ai_title when the session has been renamed', async () => {
    fetchSessions.mockResolvedValue({
      items: [{ ...SESSION_A, user_title: 'My Renamed Session' }],
      total: 1,
    })
    renderSidebar()

    expect(await screen.findByText('My Renamed Session')).toBeDefined()
    expect(screen.queryByText(SESSION_A.ai_title as string)).toBeNull()
  })

  it('falls through to the uuid-prefix when no title of any kind is set', async () => {
    fetchSessions.mockResolvedValue({
      items: [{ ...SESSION_A, ai_title: null, custom_title: null, user_title: null }],
      total: 1,
    })
    renderSidebar()

    expect(await screen.findByText(SESSION_A.session_uuid.slice(0, 8))).toBeDefined()
  })
})
