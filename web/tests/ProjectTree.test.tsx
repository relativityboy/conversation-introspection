import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { ProjectTree, type ProjectTreeProps } from '../src/components/ProjectTree'
import type { ProjectOut, SessionSummary } from '../src/api/types'

// Mocking the api client module (not global fetch) — same convention as Sidebar.test.tsx /
// ProjectFilterBar.test.tsx: hooks.ts imports these named functions directly, so replacing the
// module swaps out useProjects'/useSessions' network layer in one place.
const { fetchProjects, fetchSessions } = vi.hoisted(() => ({
  fetchProjects: vi.fn(),
  fetchSessions: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchProjects, fetchSessions }
})

// Deliberately out of alphabetical order — the component's client-side sort is what produces
// the alpha < mid < zeta ordering the tests assert (mirrors ProjectFilterBar.test.tsx's fixture).
function project(dir_slug: string, over: Partial<ProjectOut> = {}): ProjectOut {
  return { id: 0, dir_slug, resolved_cwd: null, session_count: 0, ...over }
}
const PROJECTS: ProjectOut[] = [
  project('-Users-x-zeta', { id: 2, session_count: 2 }),
  project('-Users-x-alpha', { id: 1, session_count: 41 }),
  project('-Users-x-mid', { id: 3, session_count: 0 }),
]

function session(
  uuid: string,
  projectSlug: string,
  over: Partial<SessionSummary> = {},
): SessionSummary {
  return {
    session_uuid: uuid,
    project_slug: projectSlug,
    ai_title: `Session ${uuid}`,
    custom_title: null,
    user_title: null,
    started_at: '2026-07-19T10:00:00Z',
    last_activity_at: '2026-07-19T10:30:00Z',
    message_count: 4,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
    ...over,
  }
}

function renderTree(overrides: Partial<ProjectTreeProps> = {}) {
  const queryClient = new QueryClient({
    // staleTime mirrors makeQueryClient()'s production policy (30s) — the "collapse + re-expand
    // doesn't refetch" contract is a react-query CACHE behavior, not a component-level memo, so
    // the test harness's QueryClient must carry the same staleTime the app actually runs with.
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  })
  const props: ProjectTreeProps = { q: '', fav: false, chips: [], search: '', ...overrides }
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectTree {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  // queryClient is exposed so a test can `rerender` the SAME provider/client across a props
  // change (e.g. filtered → browse) — required for the "manual expand survives" test below,
  // where ProjectTree's own useState must not be remounted out from under it.
  return { ...utils, queryClient }
}

beforeEach(() => {
  fetchProjects.mockReset()
  fetchSessions.mockReset()
})

it('renders every project alphabetically by display name with count badges', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  renderTree()

  await screen.findByText('x-alpha')

  const rows = screen.getAllByRole('button')
  expect(rows).toHaveLength(3)
  // order on screen: alpha, mid, zeta — sort correctness, and mid's zero-count project
  // still renders at all (D2, server fix from Task 1).
  expect(rows[0].textContent).toContain('x-alpha')
  expect(rows[0].textContent).toContain('41')
  expect(rows[1].textContent).toContain('x-mid')
  expect(rows[1].textContent).toContain('0')
  expect(rows[2].textContent).toContain('x-zeta')
  expect(rows[2].textContent).toContain('2')
})

it('rows start collapsed; no sessions fetch fires until expand', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  renderTree()

  await screen.findByText('x-alpha')
  expect(fetchSessions).not.toHaveBeenCalled()
})

it('expand fetches exactly that project once and renders children inTree', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  const alphaSession = session('s-alpha-1', '-Users-x-alpha', { ai_title: 'Alpha convo one' })
  fetchSessions.mockResolvedValue({ items: [alphaSession], total: 1 })

  renderTree()
  await screen.findByText('x-alpha')

  const toggle = screen.getByRole('button', { name: /x-alpha/ })
  expect(toggle.getAttribute('aria-expanded')).toBe('false')

  fireEvent.click(toggle)

  await screen.findByText('Alpha convo one')
  expect(fetchSessions).toHaveBeenCalledTimes(1)
  expect(fetchSessions).toHaveBeenCalledWith({ projects: ['-Users-x-alpha'] })
  expect(toggle.getAttribute('aria-expanded')).toBe('true')

  // inTree: the child's own project eyebrow is suppressed, so the display name appears
  // exactly once (the row label) — a second match would mean the eyebrow leaked through.
  expect(screen.getAllByText('x-alpha')).toHaveLength(1)

  // collapse, then re-expand: same react-query cache entry (staleTime not elapsed) — no
  // second network call.
  fireEvent.click(toggle)
  expect(screen.queryByText('Alpha convo one')).toBeNull()

  fireEvent.click(toggle)
  await screen.findByText('Alpha convo one')
  expect(fetchSessions).toHaveBeenCalledTimes(1)
})

it('shows "showing N of M" only when total exceeds the window', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  const manyItems = Array.from({ length: 50 }, (_, i) =>
    session(`s-alpha-${i}`, '-Users-x-alpha', { ai_title: `Alpha convo ${i}` }),
  )
  fetchSessions.mockResolvedValue({ items: manyItems, total: 213 })

  const { unmount } = renderTree()
  await screen.findByText('x-alpha')
  fireEvent.click(screen.getByRole('button', { name: /x-alpha/ }))

  await screen.findByText('showing 50 of 213')
  unmount()

  fetchProjects.mockReset()
  fetchSessions.mockReset()
  fetchProjects.mockResolvedValue(PROJECTS)
  fetchSessions.mockResolvedValue({ items: [manyItems[0]], total: 1 })

  renderTree()
  await screen.findByText('x-alpha')
  fireEvent.click(screen.getByRole('button', { name: /x-alpha/ }))

  await screen.findByText('Alpha convo 0')
  expect(screen.queryByText(/showing \d+ of \d+/)).toBeNull()
})

it('chips scope the project rows client-side', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  renderTree({ chips: ['-Users-x-zeta'] })

  await screen.findByText('x-zeta')
  expect(screen.queryByText('x-alpha')).toBeNull()
  expect(screen.queryByText('x-mid')).toBeNull()
  expect(fetchSessions).not.toHaveBeenCalled()
})

it('a failed children fetch shows an inline retry row without hiding siblings', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  const err = new ApiError(500, 'Internal Server Error', 'boom')
  fetchSessions.mockImplementation((filters: { projects?: string[] }) =>
    filters.projects?.[0] === '-Users-x-alpha'
      ? Promise.reject(err)
      : Promise.resolve({ items: [], total: 0 }),
  )

  renderTree()
  await screen.findByText('x-alpha')

  fireEvent.click(screen.getByRole('button', { name: /x-alpha/ }))

  const retry = await screen.findByRole('button', { name: 'failed to load — retry' })
  // sibling project row survives the failure — the error is scoped to alpha's subtree only.
  expect(screen.getByText('x-zeta')).toBeDefined()

  fetchSessions.mockClear()
  fireEvent.click(retry)

  await vi.waitFor(() => expect(fetchSessions).toHaveBeenCalledTimes(1))
})

// --- filtered mode: q/fav prune to ONE flat query, grouped + auto-expanded client-side --------

it('q or fav switches to ONE flat query grouped by project, auto-expanded, rows inTree', async () => {
  const alphaSession = session('s-alpha-1', '-Users-x-alpha', { ai_title: 'Alpha horizon convo' })
  const zetaSession = session('s-zeta-1', '-Users-x-zeta', { ai_title: 'Zeta horizon convo' })
  fetchSessions.mockResolvedValue({ items: [alphaSession, zetaSession], total: 2 })

  renderTree({ q: 'horizon' })

  await screen.findByText('Alpha horizon convo')
  expect(screen.getByText('Zeta horizon convo')).toBeDefined()

  // fetchProjects is never consulted in filtered mode — BrowseTree doesn't mount.
  expect(fetchProjects).not.toHaveBeenCalled()
  // ONE flat query, not one per matched project.
  expect(fetchSessions).toHaveBeenCalledTimes(1)
  expect(fetchSessions).toHaveBeenCalledWith({ q: 'horizon' })

  // both groups present and auto-expanded (children visible without any click).
  expect(screen.getByText(/x-alpha/)).toBeDefined()
  expect(screen.getByText(/x-zeta/)).toBeDefined()

  // inTree: the session row's own eyebrow is suppressed, so each project name appears exactly
  // once — the group header, not a duplicate on the row underneath it.
  expect(screen.getAllByText(/x-alpha/)).toHaveLength(1)
  expect(screen.getAllByText(/x-zeta/)).toHaveLength(1)
})

it('groups sort alphabetically and absent projects are pruned', async () => {
  const zetaSession = session('s-zeta-1', '-Users-x-zeta')
  const alphaSession = session('s-alpha-1', '-Users-x-alpha')
  // Deliberately zeta-then-alpha in the response — a client-side sort must reorder to
  // alpha-then-zeta; 'mid' never appears in the result, so it must never appear on screen.
  fetchSessions.mockResolvedValue({ items: [zetaSession, alphaSession], total: 2 })

  renderTree({ fav: true })

  await screen.findByText(/x-alpha/)

  const headers = screen.getAllByText(/^▾ /)
  expect(headers.map((h) => h.textContent)).toEqual(['▾ x-alpha', '▾ x-zeta'])
  expect(screen.queryByText(/x-mid/)).toBeNull()
})

it('snippets ride into the grouped rows', async () => {
  const withSnippet = session('s-alpha-1', '-Users-x-alpha', {
    match_snippet: 'a <mark>tidal</mark> wave',
  })
  fetchSessions.mockResolvedValue({ items: [withSnippet], total: 1 })

  const { container } = renderTree({ q: 'tidal' })
  await screen.findByText(withSnippet.ai_title as string)

  // SessionListItem does the mark-splitting work — assert it rode through untouched.
  const marks = container.querySelectorAll('mark')
  expect(marks).toHaveLength(1)
  expect(marks[0].textContent).toBe('tidal')
})

it('chips and fav thread into the query', async () => {
  fetchSessions.mockResolvedValue({ items: [], total: 0 })

  renderTree({ q: '', fav: true, chips: ['-Users-x-alpha'] })

  await screen.findByText('No conversations match')
  expect(fetchSessions).toHaveBeenCalledTimes(1)
  expect(fetchSessions).toHaveBeenCalledWith({ favorite: true, projects: ['-Users-x-alpha'] })
})

it('truncation line renders when total > items', async () => {
  const items = Array.from({ length: 12 }, (_, i) =>
    session(`s-alpha-${i}`, '-Users-x-alpha', { ai_title: `Alpha convo ${i}` }),
  )
  fetchSessions.mockResolvedValue({ items, total: 80 })

  renderTree({ q: 'x' })

  await screen.findByText('showing 12 of 80 matches')
})

it('clearing the filter restores browse mode with manual expand state intact, no refetch', async () => {
  fetchProjects.mockResolvedValue(PROJECTS)
  const zetaSession = session('s-zeta-1', '-Users-x-zeta', { ai_title: 'Zeta convo one' })
  fetchSessions.mockResolvedValue({ items: [zetaSession], total: 1 })

  const { rerender, queryClient } = renderTree()
  await screen.findByText('x-zeta')

  fireEvent.click(screen.getByRole('button', { name: /x-zeta/ }))
  await screen.findByText('Zeta convo one')
  expect(fetchSessions).toHaveBeenCalledTimes(1)

  // switch into filtered mode
  fetchSessions.mockResolvedValueOnce({ items: [], total: 0 })
  rerender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectTree q="x" fav={false} chips={[]} search="" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await screen.findByText('No conversations match')

  // clear the filter — back to browse mode
  fetchSessions.mockClear()
  rerender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectTree q="" fav={false} chips={[]} search="" />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  // zeta's manual expand state survived the roundtrip, and its cached children render with no
  // new network call (react-query cache, staleTime not elapsed).
  await screen.findByText('Zeta convo one')
  expect(fetchSessions).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /x-zeta/ }).getAttribute('aria-expanded')).toBe('true')
})
