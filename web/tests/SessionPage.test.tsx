import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { MessageList, MessageOut, SessionDetail, TranscriptInfo } from '../src/api/types'
import { SessionPage } from '../src/routes/SessionPage'
import { ALL_CATEGORIES_SET, PRESET_SETS } from '../src/lib/viewMode'

// Same convention as SubagentPage.test.tsx / Sidebar.test.tsx: mock the api client module
// (hooks.ts imports these functions directly) rather than global fetch.
const { fetchSession, fetchMessages, putArchive } = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  fetchMessages: vi.fn(),
  putArchive: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSession, fetchMessages, putArchive }
})

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: {
    totalCount: number
    firstItemIndex: number
    itemContent: (index: number) => ReactNode
  }) => (
    <div>
      {Array.from({ length: props.totalCount }, (_, i) => (
        <div key={props.firstItemIndex + i}>{props.itemContent(props.firstItemIndex + i)}</div>
      ))}
    </div>
  ),
}))

const MAIN_TRANSCRIPT: TranscriptInfo = {
  id: 1,
  kind: 'main',
  agent_hex_id: null,
  agent_type: null,
  agent_description: null,
  parent_tool_use_id: null,
}

function makeSession(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    session_uuid: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    project_slug: '-Users-x-proj',
    ai_title: 'AI Title',
    custom_title: null,
    user_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 1,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
    origin: 'root',
    transcripts: [MAIN_TRANSCRIPT],
    on_disk: true,
    ...over,
  }
}

function makeMessage(uuid: string): MessageOut {
  return {
    record_uuid: uuid,
    parent_uuid: null,
    type: 'assistant',
    model: null,
    timestamp: null,
    // Task T10: a realistic (classified) authorship_kind, not null -- the new select= default
    // (`chat` preset) has no legacy NULL-kind tolerance (categoryOfBlock floors NULL straight to
    // harness-system, which `chat` never includes), so a null-kind fixture here would silently
    // vanish under this file's real default selection. Production rows are always classified
    // post-backfill; this fixture now matches that steady state.
    authorship_kind: 'claude',
    authorship_basis: 'verified — record type assistant',
    authorship_detail: null,
    blocks: [
      {
        block_index: 0,
        block_kind: 'text',
        text_content: `text for ${uuid}`,
        tool_name: null,
        tool_use_id: null,
        is_error: null,
      },
    ],
  }
}

function pageOf(offset: number, uuids: string[], total: number): MessageList {
  return { items: uuids.map(makeMessage), total, offset }
}

// Task T17: `select=` is the only wire shape now (no `view=`) -- built from the same PRESET_SETS/
// ALL_CATEGORIES_SET the source uses.
const SELECT_CHAT = [...PRESET_SETS.chat].join(',')
const SELECT_ALL = [...ALL_CATEGORIES_SET].join(',')

beforeEach(() => {
  // useViewMode seeds from this key; a leak from a prior test would make the view start non-default.
  window.localStorage.clear()
  fetchSession.mockReset()
  fetchMessages.mockReset()
  fetchMessages.mockResolvedValue({ items: [], total: 0, offset: 0 })
  putArchive.mockReset()
  putArchive.mockResolvedValue(undefined)
})

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/s/:uuid" element={<SessionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// --- title precedence (§14.3 binding, enforced identically at every render site):
// user_title > ai_title > custom_title > uuid-prefix ------------------------------------------

describe('SessionPage header title precedence', () => {
  it('shows user_title over ai_title when the session has been renamed', async () => {
    fetchSession.mockResolvedValue(makeSession({ user_title: 'Renamed Session' }))
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    expect(await screen.findByRole('heading', { name: 'Renamed Session' })).toBeDefined()
  })

  it('falls through to the uuid-prefix when no title of any kind is set', async () => {
    fetchSession.mockResolvedValue(
      makeSession({ ai_title: null, custom_title: null, user_title: null }),
    )
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    expect(await screen.findByRole('heading', { name: 'aaaaaaaa' })).toBeDefined()
  })
})

// --- origin badge (Task T14): orientation for a deep link landing on a subagent-origin session --

describe('SessionPage origin badge', () => {
  it('shows a muted "SUBAGENT SESSION" badge when the session origin is subagent', async () => {
    fetchSession.mockResolvedValue(makeSession({ origin: 'subagent' }))
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    expect(await screen.findByText('SUBAGENT SESSION')).toBeDefined()
  })

  it('shows no badge for a root-origin session', async () => {
    fetchSession.mockResolvedValue(makeSession({ origin: 'root' }))
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    await screen.findByRole('heading', { name: 'AI Title' })
    expect(screen.queryByText('SUBAGENT SESSION')).toBeNull()
  })

  it('shows no badge for an empty-origin session', async () => {
    fetchSession.mockResolvedValue(makeSession({ origin: 'empty' }))
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    await screen.findByRole('heading', { name: 'AI Title' })
    expect(screen.queryByText('SUBAGENT SESSION')).toBeNull()
  })
})

// --- TitleEditor wiring: a thin integration check that the h1 is the real TitleEditor, not a
// static string -- the deep click/edit/esc/422 behavior matrix lives in TitleEditor.test.tsx. ---

describe('SessionPage header wiring', () => {
  it('clicking the title opens the inline editor pre-filled with the current title', async () => {
    fetchSession.mockResolvedValue(makeSession())
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    const titleButton = await screen.findByRole('button', { name: 'AI Title' })
    fireEvent.click(titleButton)

    const input = screen.getByRole('textbox', { name: 'Session title' }) as HTMLInputElement
    expect(input.value).toBe('AI Title')
  })

  it('renders the archive affordance inside the actions ▾ menu (§15.1)', async () => {
    fetchSession.mockResolvedValue(makeSession())
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    await userEvent.click(await screen.findByRole('button', { name: 'actions ▾' }))
    expect(screen.getByRole('button', { name: 'archive' })).toBeDefined()
  })
})

// --- session-id fragment: the first 8 chars of the uuid, hover reveals the full id, click copies
// it to the clipboard. -------------------------------------------------------------------------

describe('SessionPage session id fragment', () => {
  it('shows the first 8 chars of the session uuid', async () => {
    fetchSession.mockResolvedValue(makeSession())
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    const chip = await screen.findByRole('button', { name: 'copy session id' })
    expect(chip.textContent).toBe('aaaaaaaa')
  })

  it('reveals the full uuid on hover via the native title', async () => {
    fetchSession.mockResolvedValue(makeSession())
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    const chip = await screen.findByRole('button', { name: 'copy session id' })
    expect(chip.getAttribute('title')).toBe('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
  })

  it('copies the full uuid to the clipboard on click and flashes "copied"', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
    fetchSession.mockResolvedValue(makeSession())
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    const chip = await screen.findByRole('button', { name: 'copy session id' })
    fireEvent.click(chip)

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    )
    expect(await screen.findByText('copied')).toBeDefined()

    // @ts-expect-error - deleting a stubbed test-only global
    delete navigator.clipboard
  })
})

// --- 404 back-link (Phase 4 fixwave THE IMPORTANT, half 2): a genuine deep link back into the
// app -- must carry the active project filter, mirroring SubagentPage's identical link. ---------

describe('SessionPage session fetch errors', () => {
  it('preserves ?projects= on the not-found back-to-archive link', async () => {
    fetchSession.mockRejectedValue(new ApiError(404, 'Not Found', 'session x not found'))
    renderAt('/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee?projects=alpha,mid')

    expect(await screen.findByText('This conversation isn’t in the archive.')).toBeDefined()
    expect(screen.getByRole('link', { name: '← back to the archive' }).getAttribute('href')).toBe(
      '/?projects=alpha%2Cmid',
    )
  })
})

// --- archive-success navigation (F3 blemish): home is a DIRECT link, so it must carry the active
// ?projects= filter, mirroring the not-found back-link above. ---------------------------------

describe('SessionPage archive navigation', () => {
  it('preserves ?projects= when archiving navigates home on success', async () => {
    fetchSession.mockResolvedValue(makeSession())
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={['/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee?projects=alpha,mid']}
        >
          <LocationProbe />
          <Routes>
            <Route path="/s/:uuid" element={<SessionPage />} />
            <Route path="/" element={<div>home</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'actions ▾' }))
    await userEvent.click(screen.getByRole('button', { name: 'archive' }))

    expect(putArchive).toHaveBeenCalledWith('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
    await waitFor(() =>
      expect(screen.getByTestId('loc').textContent).toBe('/?projects=alpha%2Cmid'),
    )
  })
})

// --- three-state view toggle (F4 regression + critique #6) ------------------------------------
// The whole reason this file gets a toggle test: F4 proved the naive design silently no-ops when
// the header and the reader each own their own useViewMode. Switching from the HEADER must
// re-seed the READER BODY — the single-owner-per-page contract in action.

describe('SessionPage view toggle', () => {
  const PATH = '/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

  it('F4: clicking a header segment re-seeds the reader body with that view', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((_id: number, opts?: { select?: string }) =>
      Promise.resolve(pageOf(0, [opts?.select === SELECT_ALL ? 'full' : 'filtered'], 1)),
    )
    renderAt(PATH)

    // Body seeds filtered first — useCategorySelection's default is the `chat` preset. Task T17
    // item-2 revision: the preset buttons live INSIDE the dropdown panel now — open the trigger
    // (its default-state label is "View: chat") before reaching the "all" preset item.
    expect(await screen.findByText('text for filtered')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: 'View: chat' }))
    const allItem = screen.getByRole('button', { name: 'all' })
    expect(allItem.getAttribute('aria-pressed')).toBe('false')

    await userEvent.click(allItem)

    // The reader body actually re-seeded (remount + new fetch), driven purely by the header toggle.
    // The panel stays OPEN on a preset click (owner spec) — the item is still reachable to assert.
    expect(await screen.findByText('text for full')).toBeDefined()
    expect(screen.queryByText('text for filtered')).toBeNull()
    expect(allItem.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'View: all' })).not.toBeNull()
    expect(fetchMessages).toHaveBeenCalledWith(1, { offset: 0, limit: 100, select: SELECT_ALL })
  })

  it('critique #6: the count is the UNFILTERED total, unchanged by switching views', async () => {
    fetchSession.mockResolvedValue(makeSession({ message_count: 42 }))
    fetchMessages.mockResolvedValue(pageOf(0, ['m1'], 1))
    renderAt(PATH)

    // "total" states what the number MEANS in every view. Because it never appears or
    // disappears, switching views cannot reflow the row -- the count span keeps its width.
    expect(await screen.findByText('42 msgs total')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: 'View: chat' }))
    await userEvent.click(screen.getByRole('button', { name: 'all' }))
    expect(await screen.findByText('42 msgs total')).toBeDefined()
  })

  // Task T10: category selection is URL-persisted, not localStorage-sticky (the retired
  // `introspect.view.v1` mechanism is gone — zero-legacy). Task T17: the wire/URL shape for a
  // persisted non-default selection is `?select=<csv>` — there is no more pretty `?view=<preset>`
  // shorthand.
  it('URL-persisted: a session opened with ?select=<all> seeds unfiltered from first paint', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((_id: number, opts?: { select?: string }) =>
      Promise.resolve(pageOf(0, [opts?.select === SELECT_ALL ? 'full' : 'filtered'], 1)),
    )
    renderAt(`${PATH}?select=${SELECT_ALL}`)

    // Task T17 item-2 revision: the preset buttons no longer live in the header, so "is the
    // current selection the `all` preset" is read off the TRIGGER's own label instead of a
    // header button's aria-pressed.
    expect(await screen.findByText('text for full')).toBeDefined()
    expect(screen.getByRole('button', { name: 'View: all' })).not.toBeNull()
    expect(fetchMessages).toHaveBeenCalledWith(1, { offset: 0, limit: 100, select: SELECT_ALL })
    expect(fetchMessages).not.toHaveBeenCalledWith(1, { offset: 0, limit: 100, select: SELECT_CHAT })
  })

  // Task T17 (owner ruling 2026-09-25): `?view=` is deleted entirely, both read and write. A
  // legacy `?view=all` link from before this task must not be silently upgraded to the preset it
  // used to name — it's simply ignored, opening at the chat default exactly as if absent.
  it('a legacy ?view=all in the URL is ignored — opens at the chat default', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockResolvedValue(pageOf(0, ['m1'], 1))
    renderAt(`${PATH}?view=all`)

    await screen.findByText('text for m1')
    expect(screen.getByRole('button', { name: 'View: chat' })).not.toBeNull()
    expect(fetchMessages).toHaveBeenCalledWith(1, { offset: 0, limit: 100, select: SELECT_CHAT })
  })

  it('clicking a header checkbox re-seeds the reader body with the resulting custom combination', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockResolvedValue(pageOf(0, ['m1'], 1))
    renderAt(PATH)
    await screen.findByText('text for m1')

    // Task T17: the checkboxes live behind the dropdown trigger now — open it first. Default
    // selection is the `chat` preset, so the trigger reads "View: chat" (item-2 revision: the
    // exact `View: <label>` format, no trailing arrow).
    await userEvent.click(screen.getByRole('button', { name: 'View: chat' }))

    // Unchecking is the reachable custom combination here: the default `chat` preset is already
    // {you-chat, claude-chat, claude-thinking}, and every other single-box toggle from it either
    // reproduces `chat-harness` (adding harness-system) or `all` (nothing left to add) — so this
    // is deliberately an UNcheck, leaving {you-chat, claude-chat}, which matches no preset.
    await userEvent.click(screen.getByRole('checkbox', { name: 'Claude — thinking' }))

    await waitFor(() => {
      const lastCall = fetchMessages.mock.calls.at(-1)
      const select = (lastCall?.[1] as { select?: string } | undefined)?.select
      expect(new Set(select?.split(','))).toEqual(new Set(['you-chat', 'claude-chat']))
    })
  })
})
