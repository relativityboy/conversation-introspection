import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { MessageList, MessageOut, SessionDetail, TranscriptInfo } from '../src/api/types'
import { SessionPage } from '../src/routes/SessionPage'
import { SubagentPage } from '../src/routes/SubagentPage'

// Same convention as ConversationView.test.tsx / search.test.tsx / Sidebar.test.tsx: mock the api
// client module (hooks.ts imports these functions directly) rather than global fetch.
const { fetchSession, fetchMessages } = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  fetchMessages: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSession, fetchMessages }
})

// ConversationView.test.tsx documents why: jsdom has no layout engine, so the real react-virtuoso
// can't be trusted to render rows. This file doesn't re-test windowing (that's Conversation
// View's job) — it only needs itemContent to run so message text/around-seeding is observable.
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

beforeEach(() => {
  // useViewMode seeds from this key; clear it so the view starts at its default ('chat') in
  // every test.
  window.localStorage.clear()
  fetchSession.mockReset()
  fetchMessages.mockReset()
})

// --- fixtures -----------------------------------------------------------------------------

const MAIN_TRANSCRIPT: TranscriptInfo = {
  id: 1,
  kind: 'main',
  agent_hex_id: null,
  agent_type: null,
  agent_description: null,
  parent_tool_use_id: null,
}

function makeSubagentTranscript(over: Partial<TranscriptInfo> = {}): TranscriptInfo {
  return {
    id: 42,
    kind: 'subagent',
    agent_hex_id: 'deadbeef',
    agent_type: 'Explore',
    agent_description:
      'Survey the .claude jsonl format for undocumented record types and subagent sidecar files',
    parent_tool_use_id: 'toolu_1',
    ...over,
  }
}

function makeSession(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    session_uuid: 'uuid-1',
    project_slug: '-Users-x-proj',
    ai_title: 'My Session',
    custom_title: null,
    user_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 10,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
    transcripts: [MAIN_TRANSCRIPT, makeSubagentTranscript()],
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

/** A main-transcript message whose tool_use block IS the subagent dispatch: its tool_use_id
 * matches the subagent transcript's parent_tool_use_id, so the REAL SubagentChip join resolves
 * and renders the drill-in link. */
function makeDispatchMessage(): MessageOut {
  return {
    record_uuid: 'main-1',
    parent_uuid: null,
    type: 'assistant',
    model: null,
    timestamp: null,
    blocks: [
      {
        block_index: 0,
        block_kind: 'tool_use',
        text_content: null,
        tool_name: 'Task',
        tool_use_id: 'toolu_1',
        is_error: null,
      },
    ],
  }
}

function renderAt(path: string) {
  // retryDelay 0: useSession carries its own retry policy (skip 404s, else 3 tries), which
  // overrides a harness-level `retry: false` — zero delay keeps the retrying cases instant.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/s/:uuid/a/:agentHex" element={<SubagentPage />} />
          <Route path="/s/:uuid/a/:agentHex/m/:msgUuid" element={<SubagentPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// --- unknown agentHex -----------------------------------------------------------------------

describe('unknown agentHex', () => {
  it('renders an inline not-found state with a link back to the conversation, and fetches no messages', async () => {
    fetchSession.mockResolvedValueOnce(makeSession())
    renderAt('/s/uuid-1/a/zzzzzzzz')

    expect(
      await screen.findByText('This subagent transcript isn’t in the archive.'),
    ).toBeDefined()
    const link = screen.getByRole('link', { name: '← back to conversation' })
    expect(link.getAttribute('href')).toBe('/s/uuid-1')
    expect(fetchMessages).not.toHaveBeenCalled()
  })

  // Task 9: this breadcrumb is a genuine deep link back into the app (`/s/{uuid}`) — it must
  // carry the current project filter, same as every other internal link.
  it('preserves ?projects= on the not-found breadcrumb link', async () => {
    fetchSession.mockResolvedValueOnce(makeSession())
    renderAt('/s/uuid-1/a/zzzzzzzz?projects=alpha,mid')

    await screen.findByText('This subagent transcript isn’t in the archive.')
    const link = screen.getByRole('link', { name: '← back to conversation' })
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(link.getAttribute('href')).toBe('/s/uuid-1?projects=alpha%2Cmid')
  })
})

// --- session fetch errors (mirrors SessionPage's 404/offline split) --------------------------

describe('session fetch errors', () => {
  it('renders the not-found state (not "archive offline") when the session 404s', async () => {
    fetchSession.mockRejectedValue(new ApiError(404, 'Not Found', 'session uuid-1 not found'))
    renderAt('/s/uuid-1/a/deadbeef')

    expect(await screen.findByText('This conversation isn’t in the archive.')).toBeDefined()
    expect(screen.queryByText('archive offline')).toBeNull()
    expect(screen.getByRole('link', { name: '← back to the archive' }).getAttribute('href')).toBe(
      '/',
    )
  })

  it('keeps the offline text for a non-404 error', async () => {
    fetchSession.mockRejectedValue(new Error('network down'))
    renderAt('/s/uuid-1/a/deadbeef')

    expect(await screen.findByText('archive offline')).toBeDefined()
  })

  // Phase 4 fixwave THE IMPORTANT, half 2: this back-link targets "/" directly (not through
  // App.tsx's catch-all redirect), so it must carry ?projects= itself, mirroring the
  // "back to conversation" breadcrumbs above.
  it('preserves ?projects= on the back-to-archive link', async () => {
    fetchSession.mockRejectedValue(new ApiError(404, 'Not Found', 'session uuid-1 not found'))
    renderAt('/s/uuid-1/a/deadbeef?projects=alpha,mid')

    await screen.findByText('This conversation isn’t in the archive.')
    expect(screen.getByRole('link', { name: '← back to the archive' }).getAttribute('href')).toBe(
      '/?projects=alpha%2Cmid',
    )
  })
})

// --- found agentHex ---------------------------------------------------------------------------

describe('found agentHex', () => {
  it('shows the agent_type and the FULL (untruncated) description in the header', async () => {
    fetchSession.mockResolvedValueOnce(makeSession())
    fetchMessages.mockResolvedValueOnce(pageOf(0, ['m1'], 1))
    renderAt('/s/uuid-1/a/deadbeef')

    expect(await screen.findByText('⑂ Explore')).toBeDefined()
    expect(
      screen.getByText(
        'Survey the .claude jsonl format for undocumented record types and subagent sidecar files',
      ),
    ).toBeDefined()
    expect(screen.getByRole('link', { name: '← back to conversation' }).getAttribute('href')).toBe(
      '/s/uuid-1',
    )
  })

  it('preserves ?projects= on the header breadcrumb link', async () => {
    fetchSession.mockResolvedValueOnce(makeSession())
    fetchMessages.mockResolvedValueOnce(pageOf(0, ['m1'], 1))
    renderAt('/s/uuid-1/a/deadbeef?projects=alpha,mid')

    await screen.findByText('⑂ Explore')
    expect(screen.getByRole('link', { name: '← back to conversation' }).getAttribute('href')).toBe(
      '/s/uuid-1?projects=alpha%2Cmid',
    )
  })
})

// --- lazy contract ----------------------------------------------------------------------------

describe('lazy fetch contract', () => {
  it('fetches ONLY the main transcript on the session page; the subagent transcript only after drill-in', async () => {
    // The drill-in link lives inside a tool_use block, which only renders under view='all' (a
    // filtered view hides tool_use/tool_result blocks entirely, spec §5) -- seed the sticky view
    // to 'all' so this test can isolate what it's actually about (fetch laziness), independent of
    // useViewMode's own default ('chat').
    window.localStorage.setItem('introspect.view.v1', 'all')
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((transcriptId: number) =>
      Promise.resolve(
        transcriptId === MAIN_TRANSCRIPT.id
          ? { items: [makeDispatchMessage()], total: 1, offset: 0 }
          : pageOf(0, ['sub-1'], 1),
      ),
    )

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/s/uuid-1']}>
          <Routes>
            <Route path="/s/:uuid" element={<SessionPage />} />
            <Route path="/s/:uuid/a/:agentHex" element={<SubagentPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // The REAL SessionPage mounts first and loads its MAIN transcript — a surface that genuinely
    // fetches messages, so the "subagent untouched" assertion below can't pass vacuously. The
    // real SubagentChip join (tool_use_id 'toolu_1' ↔ parent_tool_use_id) renders the drill-in.
    const drillIn = await screen.findByRole('link', { name: 'view transcript →' })

    const calledIds = fetchMessages.mock.calls.map((call) => call[0])
    expect(calledIds).toContain(MAIN_TRANSCRIPT.id) // the main fetch DID fire…
    expect(calledIds).not.toContain(42) // …and the subagent transcript was never touched

    const user = userEvent.setup()
    await user.click(drillIn)

    await waitFor(() =>
      expect(fetchMessages).toHaveBeenCalledWith(42, { offset: 0, limit: 100, view: 'all' }),
    )
  })
})

// --- whitespace-only ?q= falls through to the conversation ------------------------------------

describe('SessionPage with whitespace-only ?q=', () => {
  it('renders the conversation, not an eternally-pending results panel', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockResolvedValue(pageOf(0, ['m1'], 1))

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/s/uuid-1?q=%20']}>
          <Routes>
            <Route path="/s/:uuid" element={<SessionPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // The MAIN conversation loads (useSearch would never fire for a blank q, so mounting the
    // results panel here would strand the body on a pending "…" forever).
    expect(await screen.findByText('text for m1')).toBeDefined()
    expect(fetchMessages).toHaveBeenCalledWith(MAIN_TRANSCRIPT.id, {
      offset: 0,
      limit: 100,
      view: 'chat',
    })
    expect(screen.queryByRole('button', { name: '← back to conversation' })).toBeNull()
  })
})

// --- deep link --------------------------------------------------------------------------------

describe('deep link', () => {
  it('passes the /m/:msgUuid param through as ConversationView’s around-seed', async () => {
    fetchSession.mockResolvedValueOnce(makeSession())
    fetchMessages.mockResolvedValueOnce(pageOf(3, ['m3', 'm4', 'msg-5', 'm6'], 20))
    renderAt('/s/uuid-1/a/deadbeef/m/msg-5')

    await waitFor(() =>
      expect(fetchMessages).toHaveBeenCalledWith(42, { around: 'msg-5', limit: 100, view: 'chat' }),
    )
  })
})

// --- view toggle parity (ledger #6) -------------------------------------------------------------
// SubagentPage owns its own useViewMode (one owner per reader page) exactly like SessionPage;
// switching views from its header must re-seed the subagent transcript body.

describe('SubagentPage view toggle parity', () => {
  it('renders the toggle in the header and threads the current view into the transcript fetch', async () => {
    fetchSession.mockResolvedValue(makeSession())
    fetchMessages.mockImplementation((_id: number, opts?: { view?: string }) =>
      Promise.resolve(pageOf(0, [opts?.view === 'all' ? 'sub-full' : 'sub-filtered'], 1)),
    )
    renderAt('/s/uuid-1/a/deadbeef')

    // Body seeds filtered first — useViewMode's default is 'chat'.
    expect(await screen.findByText('text for sub-filtered')).toBeDefined()
    const allSegment = screen.getByRole('button', { name: 'all' })
    expect(allSegment.getAttribute('aria-pressed')).toBe('false')

    await userEvent.click(allSegment)

    expect(await screen.findByText('text for sub-full')).toBeDefined()
    expect(allSegment.getAttribute('aria-pressed')).toBe('true')
    expect(fetchMessages).toHaveBeenCalledWith(42, { offset: 0, limit: 100, view: 'all' })
  })
})
