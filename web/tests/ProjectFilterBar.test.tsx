import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigationType } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProjectFilterBar } from '../src/components/ProjectFilterBar'
import type { ProjectOut } from '../src/api/types'

// Mock the api client module (not global fetch) — hooks.ts imports fetchProjects directly, so
// this swaps useProjects' network layer in one place. Same convention as Sidebar.test.tsx.
const { fetchProjects } = vi.hoisted(() => ({ fetchProjects: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchProjects }
})

// Deliberately out of alphabetical order in the fixture so the component's client-side sort is
// what produces the alpha < mid < zeta ordering the tests assert.
function project(dir_slug: string, over: Partial<ProjectOut> = {}): ProjectOut {
  return { id: 0, dir_slug, resolved_cwd: null, session_count: 0, ...over }
}
const PROJECTS: ProjectOut[] = [
  project('-Users-x-zeta', { id: 1, session_count: 2 }),
  project('-Users-x-alpha', { id: 2, session_count: 5 }),
  project('-Users-x-mid', { id: 3, session_count: 1 }),
]

function renderBar(initialEntries: string[] = ['/']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const locationRef: { current: { pathname: string; search: string } | null } = { current: null }
  const navTypeRef: { current: string | null } = { current: null }

  function Probe() {
    locationRef.current = useLocation()
    navTypeRef.current = useNavigationType()
    return null
  }

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <ProjectFilterBar />
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { queryClient, locationRef, navTypeRef, ...utils }
}

const input = () => screen.getByRole('searchbox') as HTMLInputElement
const revealButton = () => screen.getByRole('button', { name: 'Filter by specific projects' })

/** Reveals the SearchBox (clicks the all-projects chip's 'x') and opens the full list. */
async function openList() {
  fireEvent.click(revealButton())
  fireEvent.keyDown(input(), { key: 'ArrowDown' })
  // The options only exist once the projects query resolves; wait for the first one.
  await screen.findByRole('option', { name: '-Users-x-alpha' })
}

beforeEach(() => {
  fetchProjects.mockReset()
  fetchProjects.mockResolvedValue(PROJECTS)
})

describe('default all-projects chip', () => {
  it('renders an "all projects" chip with an x, and no searchbox, when no projects are selected', () => {
    renderBar()
    expect(screen.getByText('all projects')).toBeDefined()
    expect(revealButton()).toBeDefined()
    expect(screen.queryByRole('searchbox')).toBeNull()
  })

  it('replaces the all-projects chip with the SearchBox when its x is clicked', () => {
    renderBar()
    fireEvent.click(revealButton())
    expect(screen.queryByText('all projects')).toBeNull()
    expect(input()).toBeDefined()
  })
})

describe('opening and filtering the list', () => {
  it('opens the full alphabetized project list on ArrowDown when the box is empty', async () => {
    renderBar()
    fireEvent.click(revealButton())
    expect(input().getAttribute('aria-expanded')).toBe('false')

    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    await screen.findByRole('option', { name: '-Users-x-alpha' })

    expect(input().getAttribute('aria-expanded')).toBe('true')
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['-Users-x-alpha', '-Users-x-mid', '-Users-x-zeta'])
  })

  it('filters the list by case-insensitive substring of dir_slug (%str%, matches the middle)', async () => {
    renderBar()
    await openList()

    // 'LPH' is a middle substring of '-Users-x-alpha' and matches nothing else — proving the
    // filter is a case-insensitive substring (%str%), not a prefix.
    fireEvent.change(input(), { target: { value: 'LPH' } })

    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['-Users-x-alpha'])
  })
})

describe('selecting a project', () => {
  it('appends a chip, clears the box, keeps the list usable, and writes projects= (replace)', async () => {
    const { locationRef, navTypeRef } = renderBar()
    await openList()

    fireEvent.keyDown(input(), { key: 'Enter' }) // highlight 0 == alpha

    expect(locationRef.current?.search).toBe('?projects=-Users-x-alpha')
    expect(navTypeRef.current).toBe('REPLACE')
    expect(input().value).toBe('')
    // list stays usable: still open, with the just-selected option now excluded.
    expect(screen.getByRole('listbox')).toBeDefined()
    const chip = screen.getByRole('button', { name: 'Remove -Users-x-alpha' })
    expect(chip).toBeDefined()
    expect(screen.queryByRole('option', { name: '-Users-x-alpha' })).toBeNull()
  })

  it('selects the clicked option', async () => {
    const { locationRef } = renderBar()
    await openList()

    fireEvent.click(screen.getByRole('option', { name: '-Users-x-mid' }))

    expect(locationRef.current?.search).toBe('?projects=-Users-x-mid')
    expect(screen.getByRole('button', { name: 'Remove -Users-x-mid' })).toBeDefined()
  })

  it('navigates the list with ArrowDown/ArrowUp and selects the highlighted option on Enter', async () => {
    const { locationRef } = renderBar()
    await openList() // highlight 0 == alpha

    fireEvent.keyDown(input(), { key: 'ArrowDown' }) // -> mid
    fireEvent.keyDown(input(), { key: 'ArrowDown' }) // -> zeta
    fireEvent.keyDown(input(), { key: 'ArrowUp' }) // -> mid
    fireEvent.keyDown(input(), { key: 'Enter' })

    expect(locationRef.current?.search).toBe('?projects=-Users-x-mid')
  })
})

describe('removing chips', () => {
  it('removes a chip and updates the URL', async () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha,-Users-x-mid'])
    await screen.findByRole('button', { name: 'Remove -Users-x-alpha' })

    fireEvent.click(screen.getByRole('button', { name: 'Remove -Users-x-alpha' }))

    expect(locationRef.current?.search).toBe('?projects=-Users-x-mid')
    expect(screen.queryByRole('button', { name: 'Remove -Users-x-alpha' })).toBeNull()
  })

  it('reverts to the all-projects chip when the last chip is removed', async () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha'])
    fireEvent.click(await screen.findByRole('button', { name: 'Remove -Users-x-alpha' }))

    expect(locationRef.current?.search).toBe('')
    expect(screen.getByText('all projects')).toBeDefined()
    expect(screen.queryByRole('searchbox')).toBeNull()
  })
})

describe('unknown / stale URL slugs', () => {
  it('renders an unknown slug from the URL raw, as a chip (ledger #9)', async () => {
    renderBar(['/?projects=-Unknown-ghost-proj'])
    // Not present in the mocked projects list, yet it must render verbatim as a chip.
    expect(await screen.findByText('-Unknown-ghost-proj')).toBeDefined()
    expect(screen.getByRole('button', { name: 'Remove -Unknown-ghost-proj' })).toBeDefined()
  })
})

describe('focus management', () => {
  it('returns focus to the box after a chip is added', async () => {
    renderBar()
    await openList()
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(document.activeElement).toBe(input())
  })

  it('returns focus to the box after a (non-last) chip is removed', async () => {
    renderBar(['/?projects=-Users-x-alpha,-Users-x-mid'])
    fireEvent.click(await screen.findByRole('button', { name: 'Remove -Users-x-mid' }))
    expect(document.activeElement).toBe(input())
  })
})

// ---- The Escape machine (critique F3, binding) --------------------------------------------
//
// Single esc NEVER clears text and NEVER touches chips — at most closes the list. A double-tap
// branches on the state captured BEFORE the FIRST press of the pair. Real (non-fake) timers are
// used for the two mandatory regression tests and the single-esc test: two back-to-back
// synchronous keydowns are, by construction, <=400ms apart, so they form a genuine double-tap.
describe('the Escape machine', () => {
  it('MANDATORY: type text -> double-esc clears the text but the chips SURVIVE', async () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha'])
    await screen.findByRole('button', { name: 'Remove -Users-x-alpha' })

    fireEvent.change(input(), { target: { value: 'xy' } }) // opens the list, text present
    fireEvent.keyDown(input(), { key: 'Escape' }) // 1st: captures (list open OR text) == true
    fireEvent.keyDown(input(), { key: 'Escape' }) // 2nd: double-tap -> clear text branch

    expect(input().value).toBe('')
    expect(screen.getByRole('button', { name: 'Remove -Users-x-alpha' })).toBeDefined()
    expect(locationRef.current?.search).toBe('?projects=-Users-x-alpha')
  })

  it('MANDATORY: empty box + closed list -> double-esc removes ALL chips', async () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha,-Users-x-mid'])
    await screen.findByRole('button', { name: 'Remove -Users-x-alpha' })
    // box is empty and the list is closed (no typing, no ArrowDown)

    fireEvent.keyDown(input(), { key: 'Escape' }) // 1st: captures (closed, empty) == false
    fireEvent.keyDown(input(), { key: 'Escape' }) // 2nd: double-tap -> remove-all branch

    expect(locationRef.current?.search).toBe('')
    expect(screen.getByText('all projects')).toBeDefined()
  })

  it('single esc closes the list without clearing the text or touching chips', async () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha'])
    await screen.findByRole('button', { name: 'Remove -Users-x-alpha' })

    fireEvent.change(input(), { target: { value: 'xy' } })
    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    await screen.findByRole('listbox')

    fireEvent.keyDown(input(), { key: 'Escape' }) // single -> close list only

    expect(screen.queryByRole('listbox')).toBeNull()
    expect(input().value).toBe('xy')
    expect(screen.getByRole('button', { name: 'Remove -Users-x-alpha' })).toBeDefined()
    expect(locationRef.current?.search).toBe('?projects=-Users-x-alpha')
  })
})

describe('the Escape machine — 400ms timing boundary', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // Chips come from the URL, not the query — keep the projects fetch pending so no async state
    // update lands mid-test to muddy the fake-clock arithmetic (or the act() boundary).
    fetchProjects.mockReturnValue(new Promise(() => {}))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('fires the double-tap when the second esc lands within 400ms (removes all chips)', () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha,-Users-x-mid'])
    expect(screen.getByRole('button', { name: 'Remove -Users-x-alpha' })).toBeDefined()

    fireEvent.keyDown(input(), { key: 'Escape' })
    vi.advanceTimersByTime(400)
    fireEvent.keyDown(input(), { key: 'Escape' })

    expect(locationRef.current?.search).toBe('')
  })

  it('does NOT fire the double-tap when the second esc lands after 400ms (chips survive)', () => {
    const { locationRef } = renderBar(['/?projects=-Users-x-alpha,-Users-x-mid'])
    expect(screen.getByRole('button', { name: 'Remove -Users-x-alpha' })).toBeDefined()

    fireEvent.keyDown(input(), { key: 'Escape' })
    vi.advanceTimersByTime(401)
    fireEvent.keyDown(input(), { key: 'Escape' })

    // Two independent singles: each only closes the (already-closed) list. Chips untouched.
    expect(locationRef.current?.search).toBe('?projects=-Users-x-alpha,-Users-x-mid')
    expect(screen.getByRole('button', { name: 'Remove -Users-x-alpha' })).toBeDefined()
  })
})
