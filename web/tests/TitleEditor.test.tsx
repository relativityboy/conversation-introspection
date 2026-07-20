import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { SessionSummary } from '../src/api/types'
import { TitleEditor } from '../src/components/TitleEditor'

// Mock the api client module (not global fetch) — same convention as Sidebar.test.tsx /
// ProjectFilterBar.test.tsx: hooks.ts (useSessionTitle) imports putSessionTitle directly, so
// this swaps its network layer in one place while the real useMutation/invalidation logic runs.
const { putSessionTitle } = vi.hoisted(() => ({ putSessionTitle: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, putSessionTitle }
})

const SESSION: SessionSummary = {
  session_uuid: 'uuid-1',
  project_slug: '-Users-x-proj',
  ai_title: 'AI Title',
  custom_title: null,
  user_title: null,
  started_at: null,
  last_activity_at: null,
  message_count: 3,
  favorite: false,
  match_snippet: null,
}

// A realistic-length uuid so the uuid-prefix fallback (first 8 chars) is distinct from the
// whole string — proves the slice, not just presence of SOME text.
const SESSION_NO_TITLE: SessionSummary = {
  ...SESSION,
  session_uuid: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
  ai_title: null,
  custom_title: null,
}

function renderEditor(over: Partial<SessionSummary> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TitleEditor session={{ ...SESSION, ...over }} />
    </QueryClientProvider>,
  )
  return { queryClient, ...utils }
}

const titleButton = (name: string | RegExp) => screen.getByRole('button', { name })
const titleInput = () => screen.getByRole('textbox', { name: 'Session title' }) as HTMLInputElement

beforeEach(() => {
  putSessionTitle.mockReset()
  putSessionTitle.mockResolvedValue(undefined)
})

// --- click-to-edit + prefill (§14.3 binding: prefill is the DISPLAY title, and NEVER the
// uuid-prefix for a title-less session) --------------------------------------------------------

describe('click-to-edit', () => {
  it('opens an input pre-filled with the archive title for an unrenamed session', () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    expect(titleInput().value).toBe('AI Title')
  })

  it('opens an input pre-filled with the user_title for an already-renamed session', () => {
    renderEditor({ user_title: 'Renamed' })
    fireEvent.click(titleButton('Renamed'))
    expect(titleInput().value).toBe('Renamed')
  })

  it('prefills EMPTY, never the uuid-prefix, for a title-less session', () => {
    renderEditor(SESSION_NO_TITLE)
    fireEvent.click(titleButton('aaaaaaaa'))
    expect(titleInput().value).toBe('')
  })

  it('is keyboard-reachable — Enter on the focused title button opens the editor', async () => {
    const user = userEvent.setup()
    renderEditor()
    titleButton('AI Title').focus()

    await user.keyboard('{Enter}')

    expect(screen.getByRole('textbox', { name: 'Session title' })).toBeDefined()
  })
})

// --- committing --------------------------------------------------------------------------------

describe('committing an edit', () => {
  it('Enter commits the mutation with the typed value', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.change(titleInput(), { target: { value: 'New Name' } })
    fireEvent.keyDown(titleInput(), { key: 'Enter' })

    await waitFor(() => expect(putSessionTitle).toHaveBeenCalledWith('uuid-1', 'New Name'))
  })

  it('blur commits when the value changed', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.change(titleInput(), { target: { value: 'New Name' } })
    fireEvent.blur(titleInput())

    await waitFor(() => expect(putSessionTitle).toHaveBeenCalledWith('uuid-1', 'New Name'))
  })

  it('blur does NOT commit (and closes quietly) when the value is unchanged', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.blur(titleInput())

    // Give any (wrong) async mutation call a beat before asserting it never fired.
    await Promise.resolve()
    expect(putSessionTitle).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  // Named risk: a blur that follows an Enter-triggered commit must not fire a second mutation.
  // Enter and blur fire back-to-back with NO await in between — committedRef is set the instant
  // Enter's synchronous handler runs, so the guard holds regardless of whether react-query's
  // internal (microtask-deferred) call to the mutationFn has happened yet. The waitFor at the
  // end only lets that deferred call surface so the count can be asserted at all.
  it('does not double-fire the mutation when blur follows Enter', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.change(titleInput(), { target: { value: 'New Name' } })
    fireEvent.keyDown(titleInput(), { key: 'Enter' })
    fireEvent.blur(titleInput())

    await waitFor(() => expect(putSessionTitle).toHaveBeenCalledTimes(1))
    expect(putSessionTitle).toHaveBeenCalledWith('uuid-1', 'New Name')
  })

  it('closes the editor back to the title button after a successful commit', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.change(titleInput(), { target: { value: 'New Name' } })
    fireEvent.keyDown(titleInput(), { key: 'Enter' })

    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
    expect(screen.getByRole('button')).toBeDefined()
  })
})

// --- single Escape: instant cancel, no mutation -------------------------------------------------

describe('single Escape', () => {
  it('cancels without committing and reverts to the button showing the ORIGINAL title', async () => {
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    fireEvent.change(titleInput(), { target: { value: 'Discard me' } })
    fireEvent.keyDown(titleInput(), { key: 'Escape' })

    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByRole('button', { name: 'AI Title' })).toBeDefined()
    await Promise.resolve()
    expect(putSessionTitle).not.toHaveBeenCalled()
  })
})

// --- the F5 esc mechanism (binding): first esc closes + installs a document listener that
// self-removes after 400ms; a second esc arriving through THAT listener clears the title.
// Deliberately a different machine from ProjectFilterBar's (that one never closes on the first
// press); this one closes immediately, then listens. -------------------------------------------

describe('the F5 esc mechanism — 400ms window', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('esc, esc at 399ms clears the title (fires title: "")', async () => {
    renderEditor({ user_title: 'Renamed' })
    fireEvent.click(titleButton('Renamed'))

    fireEvent.keyDown(titleInput(), { key: 'Escape' })
    vi.advanceTimersByTime(399)
    fireEvent.keyDown(document, { key: 'Escape' })

    // mutate() fires synchronously here, but react-query defers the actual mutationFn call
    // (putSessionTitle) by a microtask — flush it before asserting, same as the double-commit
    // guard test above. Fake timers don't fake microtasks, so a bare await is enough.
    await Promise.resolve()
    expect(putSessionTitle).toHaveBeenCalledWith('uuid-1', '')
  })

  it('esc, esc at 401ms does NOT clear — the listener already expired', () => {
    renderEditor({ user_title: 'Renamed' })
    fireEvent.click(titleButton('Renamed'))

    fireEvent.keyDown(titleInput(), { key: 'Escape' })
    vi.advanceTimersByTime(401)
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(putSessionTitle).not.toHaveBeenCalledWith('uuid-1', '')
  })

  it('esc -> click elsewhere -> esc does NOT clear — any other interaction breaks the chord', () => {
    renderEditor({ user_title: 'Renamed' })
    fireEvent.click(titleButton('Renamed'))

    fireEvent.keyDown(titleInput(), { key: 'Escape' })
    fireEvent.mouseDown(document.body)
    vi.advanceTimersByTime(100)
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(putSessionTitle).not.toHaveBeenCalledWith('uuid-1', '')
  })

  it('removes the document listener on unmount — a later esc after navigating away is inert', () => {
    const { unmount } = renderEditor({ user_title: 'Renamed' })
    fireEvent.click(titleButton('Renamed'))
    fireEvent.keyDown(titleInput(), { key: 'Escape' })

    unmount()
    vi.advanceTimersByTime(100)
    expect(() => fireEvent.keyDown(document, { key: 'Escape' })).not.toThrow()

    expect(putSessionTitle).not.toHaveBeenCalledWith('uuid-1', '')
  })
})

// --- the "edited" dot ----------------------------------------------------------------------

describe('the edited dot', () => {
  it('renders no dot for a session with no user_title', () => {
    const { container } = renderEditor()
    expect(container.querySelector('.title-edited-dot')).toBeNull()
  })

  it('renders a dot whose title= attribute is the archive original (ai_title chain, not user_title)', () => {
    const { container } = renderEditor({ user_title: 'Renamed', ai_title: 'AI Title' })
    const dot = container.querySelector('.title-edited-dot')
    expect(dot).not.toBeNull()
    expect(dot?.getAttribute('title')).toBe('AI Title')
  })
})

// --- 422 handling ----------------------------------------------------------------------------

describe('422 (title too long)', () => {
  it('keeps the editor open, preserves the value, and shows the problem detail inline', async () => {
    putSessionTitle.mockRejectedValueOnce(
      new ApiError(422, 'Unprocessable Entity', 'title must be at most 200 characters'),
    )
    renderEditor()
    fireEvent.click(titleButton('AI Title'))
    const tooLong = 'x'.repeat(201)
    fireEvent.change(titleInput(), { target: { value: tooLong } })
    fireEvent.keyDown(titleInput(), { key: 'Enter' })

    expect(await screen.findByText('title must be at most 200 characters')).toBeDefined()
    expect(titleInput().value).toBe(tooLong)
  })
})
