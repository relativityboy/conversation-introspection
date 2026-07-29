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
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectTree {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
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
