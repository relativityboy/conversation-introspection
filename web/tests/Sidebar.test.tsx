import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { ProjectFilterBar } from '../src/components/ProjectFilterBar'
import { Sidebar } from '../src/components/Sidebar'
import type { SessionSummary } from '../src/api/types'

// Same key sidebarTree.ts writes to (mirrors sidebarTree.test.ts's own local KEY constant — the
// module exports no public name for it, by design: it's an implementation detail of the ONE
// owner hook, not a value other modules should import and depend on).
const SIDEBAR_TREE_KEY = 'introspect.sidebarTree.v1'

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
  match_record_uuid: null,
  match_agent_hex_id: null,
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
  match_record_uuid: 'rec-tidal',
  match_agent_hex_id: null,
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
})

// The debounce/echo-guard tests moved to TopbarSearch.test.tsx (Task 4) — TopbarSearch now owns
// the input and every ?filter= write. Sidebar only READS ?filter= (readSidebarParams), so its
// tests drive the query straight off the URL via initialEntries, with no debounce to wait out.
describe('content filter (read from URL)', () => {
  beforeEach(() => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
  })

  it('queries with the URL filter immediately on mount, no debounce wait', async () => {
    renderSidebar(['/?filter=zzz'])
    await vi.waitFor(() =>
      expect(fetchSessions).toHaveBeenCalledWith(expect.objectContaining({ q: 'zzz' })),
    )
  })

  // Zero-legacy ruling (relativityboy, ledger #4): the retired `?title=` key must NOT resurrect a
  // filter. A deep link built against the old contract queries unfiltered.
  it('does not read the retired `?title=` param', async () => {
    renderSidebar(['/?title=zzz'])
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

// THE IMPORTANT (Phase 4 fixwave, half 2): the logo link must carry the active project filter
// through App.tsx's catch-all redirect (readProjects/writeProjects), not shed it at the link.
describe('logo link', () => {
  it('carries the current ?projects= filter on the logo link href when chips are active', async () => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
    renderSidebar(['/?projects=alpha,mid'])
    await screen.findByText('Archive is empty — run introspect import')

    const link = screen.getByRole('link', { name: 'conversation-introspection' })
    expect(link.getAttribute('href')).toBe('/?projects=alpha%2Cmid')
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

// --- by-project toggle (Task 7): the toggle pill, the flat/tree body switch it drives, and the
// localStorage persistence useSidebarTree already owns (this is Sidebar's ONE call site) --------

describe('by-project toggle', () => {
  afterEach(() => window.localStorage.removeItem(SIDEBAR_TREE_KEY))

  it('renders the by-project toggle with aria-pressed from storage', async () => {
    window.localStorage.setItem(SIDEBAR_TREE_KEY, '1')
    fetchProjects.mockResolvedValue([])
    fetchSessions.mockResolvedValue({ items: [SESSION_A], total: 1 })

    const { container } = renderSidebar()

    const toggle = screen.getByRole('button', { name: 'by project' })
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    await vi.waitFor(() => expect(fetchProjects).toHaveBeenCalledTimes(1))
    // The tree body renders instead of the flat list -- SESSION_A resolves via fetchSessions
    // (Sidebar's own hook call is unconditional) but must not appear as a flat row.
    expect(container.querySelector('.convo-list')).toBeNull()
    expect(screen.queryByText(SESSION_A.ai_title as string)).toBeNull()
  })

  it('toggle click flips mode, persists, and swaps the body', async () => {
    fetchSessions.mockResolvedValue({ items: [], total: 0 })
    fetchProjects.mockResolvedValue([])

    const { container } = renderSidebar()
    await screen.findByText('Archive is empty — run introspect import')
    expect(fetchProjects).not.toHaveBeenCalled()
    expect(container.querySelector('.convo-list')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'by project' }))

    await vi.waitFor(() => expect(fetchProjects).toHaveBeenCalledTimes(1))
    expect(window.localStorage.getItem(SIDEBAR_TREE_KEY)).toBe('1')
    expect(container.querySelector('.convo-list')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'by project' }))

    expect(window.localStorage.getItem(SIDEBAR_TREE_KEY)).toBeNull()
    expect(container.querySelector('.convo-list')).not.toBeNull()
  })

  // Regression pin: flat mode (the toggle OFF, today's default) must render exactly as it did
  // before this task -- adding the toggle button and the tree branch must not perturb the
  // pre-existing flat list output in any way.
  it('flat mode is byte-identical to today', async () => {
    fetchSessions.mockResolvedValue({ items: [SESSION_A, SESSION_B], total: 2 })
    const { container } = renderSidebar()
    await screen.findByText(SESSION_B.ai_title as string)

    const toggle = screen.getByRole('button', { name: 'by project' })
    expect(toggle.getAttribute('aria-pressed')).toBe('false')
    expect(fetchProjects).not.toHaveBeenCalled()

    const list = container.querySelector('.convo-list')
    expect(list).not.toBeNull()
    expect(list?.children).toHaveLength(2)

    // Same snippet/mark assertions as the "content-match snippet hint" suite above, re-run here
    // under the toggle's presence to prove the flat rendering path itself is untouched.
    const marks = container.querySelectorAll('mark')
    expect(marks).toHaveLength(1)
    expect(marks[0].textContent).toBe('tidal')
    expect(container.querySelector('.convo-snippet-hint')?.textContent).toBe(
      'a tidal wave of changes',
    )
  })
})
