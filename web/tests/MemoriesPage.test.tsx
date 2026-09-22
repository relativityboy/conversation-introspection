import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { MemoryList, MemoryOut, MemoryProjectOut } from '../src/api/types'
import { MemoriesPage } from '../src/routes/MemoriesPage'

// Same convention as search.test.tsx / SessionPage.test.tsx: mock the api client module (hooks.ts
// imports fetchMemories directly) rather than global fetch.
const { fetchMemories } = vi.hoisted(() => ({ fetchMemories: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchMemories }
})

beforeEach(() => {
  fetchMemories.mockReset()
})

// --- fixtures ---------------------------------------------------------------------------------

function makeMemory(over: Partial<MemoryOut> = {}): MemoryOut {
  return {
    name: 'note',
    description: 'a note',
    type: null,
    filename: 'note.md',
    path: '/Users/x/projects/alpha/memory/note.md',
    size: 42,
    mtime: '2026-09-01T00:00:00Z',
    body: 'body text',
    error: null,
    ...over,
  }
}

function makeProject(over: Partial<MemoryProjectOut> = {}): MemoryProjectOut {
  return {
    dir_slug: '-Users-x-projects-alpha',
    resolved_cwd: '/Users/x/projects/alpha',
    memories: [],
    ...over,
  }
}

const KEEP_DIFFS = makeMemory({
  name: 'keep-diffs-small',
  type: 'feedback',
  description: 'Keep diffs small',
  filename: 'keep-diffs-small.md',
  path: '/Users/x/projects/alpha/memory/keep-diffs-small.md',
  body: '**Why:** small',
})

const BARE_NOTE = makeMemory({
  name: 'bare-note',
  type: null,
  description: null,
  filename: 'bare-note.md',
  path: '/Users/x/projects/alpha/memory/bare-note.md',
  body: 'plain',
})

const GO_EXPERIENCE = makeMemory({
  name: 'go-experience',
  type: 'user',
  description: 'Notes from Go work',
  filename: 'go-experience.md',
  path: '/Users/x/projects/beta/memory/go-experience.md',
  body: null,
  error: 'PermissionError',
})

const ALPHA = makeProject({
  dir_slug: '-Users-x-projects-alpha',
  resolved_cwd: '/Users/x/projects/alpha',
  memories: [KEEP_DIFFS, BARE_NOTE],
})

const BETA = makeProject({
  dir_slug: '-Users-x-projects-beta',
  resolved_cwd: null,
  memories: [GO_EXPERIENCE],
})

function fullData(): MemoryList {
  return { projects: [ALPHA, BETA] }
}

// --- harness ----------------------------------------------------------------------------------

function setup(path = '/memories') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <MemoriesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// --- tests --------------------------------------------------------------------------------

describe('MemoriesPage', () => {
  it('groups render with project labels and counts; all three cards present', async () => {
    fetchMemories.mockResolvedValue(fullData())
    setup()

    // beta's resolved_cwd is null (by design -- exercises projectLabel's slug-tail fallback), so
    // its label is the "-Users-" tail of the slug, not a bare "beta" (see projectName.test.ts).
    expect(await screen.findByRole('heading', { name: 'alpha (2)' })).toBeDefined()
    expect(screen.getByRole('heading', { name: 'x-projects-beta (1)' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'keep-diffs-small' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'bare-note' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'go-experience' })).toBeDefined()
  })

  it('typing "diffs" in the filter leaves only keep-diffs-small', async () => {
    fetchMemories.mockResolvedValue(fullData())
    const user = userEvent.setup()
    setup()

    await screen.findByRole('button', { name: 'keep-diffs-small' })
    await user.type(screen.getByRole('textbox', { name: 'Filter memories' }), 'diffs')

    expect(screen.getByRole('button', { name: 'keep-diffs-small' })).toBeDefined()
    expect(screen.queryByRole('button', { name: 'bare-note' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'go-experience' })).toBeNull()
    expect(screen.queryByRole('heading', { name: /beta/ })).toBeNull()
  })

  it('type chips filter by type; toggling off restores all; the untyped chip isolates null-typed memories', async () => {
    fetchMemories.mockResolvedValue(fullData())
    const user = userEvent.setup()
    setup()

    await screen.findByRole('button', { name: 'keep-diffs-small' })

    const userChip = screen.getByRole('button', { name: 'user' })
    expect(userChip.getAttribute('aria-pressed')).toBe('false')
    await user.click(userChip)

    expect(userChip.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'go-experience' })).toBeDefined()
    expect(screen.queryByRole('button', { name: 'keep-diffs-small' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'bare-note' })).toBeNull()

    await user.click(userChip)
    expect(userChip.getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'keep-diffs-small' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'bare-note' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'go-experience' })).toBeDefined()

    await user.click(screen.getByRole('button', { name: 'untyped' }))
    expect(screen.getByRole('button', { name: 'bare-note' })).toBeDefined()
    expect(screen.queryByRole('button', { name: 'keep-diffs-small' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'go-experience' })).toBeNull()
  })

  it('?projects= shows only the requested project', async () => {
    fetchMemories.mockResolvedValue(fullData())
    setup('/memories?projects=-Users-x-projects-beta')

    expect(await screen.findByRole('button', { name: 'go-experience' })).toBeDefined()
    expect(screen.queryByRole('button', { name: 'keep-diffs-small' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'bare-note' })).toBeNull()
    expect(screen.queryByRole('heading', { name: /alpha/ })).toBeNull()
  })

  it('expanding a card renders its markdown body; a null body shows "body unavailable"', async () => {
    fetchMemories.mockResolvedValue(fullData())
    const user = userEvent.setup()
    setup()

    await user.click(await screen.findByRole('button', { name: 'keep-diffs-small' }))
    expect(screen.getByText('Why:').tagName).toBe('STRONG')

    await user.click(screen.getByRole('button', { name: 'go-experience' }))
    expect(screen.getByText('body unavailable')).toBeDefined()
  })

  it('shows an unreadable-error line for a memory with a non-null error', async () => {
    fetchMemories.mockResolvedValue(fullData())
    setup()

    expect(await screen.findByText('unreadable: PermissionError')).toBeDefined()
  })

  describe('copy chip', () => {
    beforeEach(() => {
      // shouldAdvanceTime keeps testing-library's own setTimeout-based findBy/waitFor polling
      // alive while still letting the test fast-forward the 1600ms revert -- same convention as
      // StatusBar.test.tsx's import-trigger suite.
      vi.useFakeTimers({ shouldAdvanceTime: true })
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('copies the memory path and flashes "copied" for 1600ms', async () => {
      // Plain `Object.assign` would throw here: earlier tests in this file call
      // `userEvent.setup()`, which (unlike the direct `userEvent.click` calls elsewhere in the
      // app's tests) stubs `navigator.clipboard` as a getter-only accessor for the rest of the
      // file (@testing-library/user-event's Clipboard.js, detached only in its own `afterAll`).
      // `defineProperty` replaces that accessor outright instead of assigning through it.
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: vi.fn().mockResolvedValue(undefined) },
        configurable: true,
      })
      fetchMemories.mockResolvedValue(fullData())
      setup()

      const chip = await screen.findByRole('button', {
        name: 'copy path for keep-diffs-small.md',
      })
      fireEvent.click(chip)

      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(KEEP_DIFFS.path)
      expect(await screen.findByText('copied')).toBeDefined()

      await vi.advanceTimersByTimeAsync(1600)
      expect(screen.queryByText('copied')).toBeNull()

      // @ts-expect-error - deleting a stubbed test-only global
      delete navigator.clipboard
    })
  })

  it('shows "No memory files found" when there is no data at all', async () => {
    fetchMemories.mockResolvedValue({ projects: [] } satisfies MemoryList)
    setup()

    expect(await screen.findByText('No memory files found')).toBeDefined()
  })

  it('shows "No memories match" when filters remove every memory', async () => {
    fetchMemories.mockResolvedValue(fullData())
    const user = userEvent.setup()
    setup()

    await screen.findByRole('button', { name: 'keep-diffs-small' })
    await user.type(screen.getByRole('textbox', { name: 'Filter memories' }), 'zzzzz')

    expect(await screen.findByText('No memories match')).toBeDefined()
  })

  it('renders the same error surface other pages use when the hook errors', async () => {
    fetchMemories.mockRejectedValue(new ApiError(500, 'Internal Server Error', 'boom'))
    setup()

    expect(await screen.findByText('archive offline')).toBeDefined()
  })
})
