import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { MessageList, MessageOut, SessionDetail, TranscriptInfo } from '../src/api/types'
import { SessionPage } from '../src/routes/SessionPage'

// Same convention as SubagentPage.test.tsx / Sidebar.test.tsx: mock the api client module
// (hooks.ts imports these functions directly) rather than global fetch.
const { fetchSession, fetchMessages } = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  fetchMessages: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSession, fetchMessages }
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
    transcripts: [MAIN_TRANSCRIPT],
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

beforeEach(() => {
  // useChatOnly seeds from this key; a leak from a prior test would make the toggle start ON.
  window.localStorage.clear()
  fetchSession.mockReset()
  fetchMessages.mockReset()
  fetchMessages.mockResolvedValue({ items: [], total: 0, offset: 0 })
})

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

// --- conversation-only toggle (F4 regression + critique #6) -----------------------------------
// The whole reason this file gets a toggle test: F4 proved the naive design silently no-ops when
// the header and the reader each own their own useChatOnly. Toggling from the HEADER must re-seed
// the READER BODY — the single-owner-per-page contract in action.

describe('SessionPage conversation-only toggle', () => {
  const PATH = '/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

  it('F4: toggling the header control re-seeds the reader body with chat_only', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((_id: number, opts?: { chat_only?: boolean }) =>
      Promise.resolve(pageOf(0, [opts?.chat_only ? 'filtered' : 'full'], 1)),
    )
    renderAt(PATH)

    // Body seeds unfiltered first.
    expect(await screen.findByText('text for full')).toBeDefined()
    const toggle = screen.getByRole('button', { name: 'conversation only' })
    expect(toggle.getAttribute('aria-pressed')).toBe('false')

    await userEvent.click(toggle)

    // The reader body actually re-seeded (remount + new fetch), driven purely by the header toggle.
    expect(await screen.findByText('text for filtered')).toBeDefined()
    expect(screen.queryByText('text for full')).toBeNull()
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(fetchMessages).toHaveBeenCalledWith(1, { offset: 0, limit: 100, chat_only: true })
  })

  it('critique #6: keeps the UNFILTERED message_count and appends "· conversation only" while active', async () => {
    fetchSession.mockResolvedValue(makeSession({ message_count: 42 }))
    fetchMessages.mockResolvedValue(pageOf(0, ['m1'], 1))
    renderAt(PATH)

    expect(await screen.findByText('42 msgs')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: 'conversation only' }))
    // Still the unfiltered 42 — no second server count — now with the mist suffix.
    expect(await screen.findByText('42 msgs · conversation only')).toBeDefined()
  })

  it('is sticky: a session opened while the stored flag is ON seeds filtered from first paint', async () => {
    window.localStorage.setItem('introspect.chatOnly.v1', '1')
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((_id: number, opts?: { chat_only?: boolean }) =>
      Promise.resolve(pageOf(0, [opts?.chat_only ? 'filtered' : 'full'], 1)),
    )
    renderAt(PATH)

    expect(await screen.findByText('text for filtered')).toBeDefined()
    expect(screen.getByRole('button', { name: 'conversation only' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    expect(fetchMessages).toHaveBeenCalledWith(1, { offset: 0, limit: 100, chat_only: true })
    expect(fetchMessages).not.toHaveBeenCalledWith(1, { offset: 0, limit: 100 })
  })
})
