import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { forwardRef, useImperativeHandle, useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type MessagesOptions } from '../src/api/client'
import type { MessageList, MessageOut } from '../src/api/types'
import { ConversationView } from '../src/components/reader/ConversationView'

// Windowing is tested at the LOGIC level per the task contract: jsdom has no layout engine, so
// real react-virtuoso can never honestly fire startReached/endReached from scrolling. The mock
// captures the props ConversationView hands to Virtuoso (firstItemIndex, totalCount,
// initialTopMostItemIndex) and exposes the two edge callbacks as buttons; every row the real
// component would virtualize is rendered flat so prepend/append order is observable in the DOM.
//
// initialTopMostItemIndex is a PLAIN NUMBER only (walk fix 9b) — the object `{index, align}` form
// livelocked the main thread on at least one profile-dependent Chrome configuration, so
// ConversationView now lands at the plain top-edge index and re-centers imperatively via a
// scrollToIndex ref call once mounted. The mock exposes that ref (forwardRef +
// useImperativeHandle) so scrollToIndexMock can pin what was requested.
interface VirtuosoMockProps {
  totalCount: number
  firstItemIndex: number
  initialTopMostItemIndex?: number
  startReached?: (index: number) => void
  endReached?: (index: number) => void
  itemContent: (index: number) => ReactNode
}

const { fetchMessages, fetchRawRecord, virtuosoProps, scrollToIndexMock } = vi.hoisted(() => ({
  fetchMessages: vi.fn(),
  fetchRawRecord: vi.fn(),
  virtuosoProps: { current: null as VirtuosoMockProps | null },
  scrollToIndexMock: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchMessages, fetchRawRecord }
})

vi.mock('react-virtuoso', () => ({
  Virtuoso: forwardRef<{ scrollToIndex: typeof scrollToIndexMock }, VirtuosoMockProps>(
    (props, ref) => {
      virtuosoProps.current = props
      useImperativeHandle(ref, () => ({ scrollToIndex: scrollToIndexMock }))
      return (
        <div>
          <button onClick={() => props.startReached?.(props.firstItemIndex)}>reach-start</button>
          {Array.from({ length: props.totalCount }, (_, i) => (
            <div data-testid="row" key={props.firstItemIndex + i}>
              {props.itemContent(props.firstItemIndex + i)}
            </div>
          ))}
          <button onClick={() => props.endReached?.(props.firstItemIndex + props.totalCount - 1)}>
            reach-end
          </button>
        </div>
      )
    },
  ),
}))

const TRANSCRIPT_ID = 7

function makeMessage(ordinal: number): MessageOut {
  return {
    record_uuid: `uuid-${ordinal}`,
    parent_uuid: null,
    type: ordinal % 2 === 0 ? 'user' : 'assistant',
    model: null,
    timestamp: null,
    blocks: [
      {
        block_index: 0,
        block_kind: 'text',
        text_content: `message ${ordinal}`,
        tool_name: null,
        tool_use_id: null,
        is_error: null,
      },
    ],
  }
}

function pageOf(offset: number, count: number, total: number): MessageList {
  return {
    items: Array.from({ length: count }, (_, i) => makeMessage(offset + i)),
    total,
    offset,
  }
}

function renderView(
  initialAroundUuid?: string,
  chatOnly = false,
  setChatOnly: (value: boolean) => void = () => {},
) {
  // We deliberately DON'T disable retry here: useMessages owns the retry policy (skip 404s,
  // else the default 3), and the 404/offline cases below exist to exercise exactly that.
  // retryDelay 0 keeps the retrying (non-404) case instant instead of backing off for seconds.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ConversationView
        transcriptId={TRANSCRIPT_ID}
        initialAroundUuid={initialAroundUuid}
        chatOnly={chatOnly}
        setChatOnly={setChatOnly}
      />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  fetchMessages.mockReset()
  fetchRawRecord.mockReset()
  virtuosoProps.current = null
  scrollToIndexMock.mockReset()
})

describe('initial load without around', () => {
  it('fetches offset 0 and starts the window at firstItemIndex 0', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView()

    expect(await screen.findAllByTestId('row')).toHaveLength(100)
    expect(fetchMessages).toHaveBeenCalledTimes(1)
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, { offset: 0, limit: 100 })
    expect(virtuosoProps.current?.firstItemIndex).toBe(0)
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(0)
  })
})

describe('around-seeded load', () => {
  it('seeds firstItemIndex from the response offset and lands at the array-local target index', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90')

    expect(await screen.findAllByTestId('row')).toHaveLength(100)
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, { around: 'uuid-90', limit: 100 })
    expect(virtuosoProps.current?.firstItemIndex).toBe(40)
    // uuid-90 sits at array index 50 within the seeded page (items are uuid-40..uuid-139). A
    // plain number, not the object form (walk fix 9b round 3 — that livelocked at least one
    // Chrome profile), and ARRAY-LOCAL, not offset-adjusted (walk fix 9b round 4 — see the NOTE
    // at ConversationView.tsx's `targetIndex`: an offset-adjusted ("absolute") index was tried
    // and empirically disproven — it broke real nonzero-offset deeplinks instead of fixing them).
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(50)
  })

  it('re-centers on the array-local target index once, imperatively, after mount', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90')
    await screen.findAllByTestId('row')

    // The centering (2026-07-20 walk ruling — top-edge landing hid the context above) happens via
    // a one-shot scrollToIndex ref call (walk fix 9b round 3), with an array-local index argument
    // (walk fix 9b round 4 confirmed this, not offset + arrayIndex).
    await waitFor(() =>
      expect(scrollToIndexMock).toHaveBeenCalledWith({ index: 50, align: 'center', behavior: 'auto' }),
    )
    expect(scrollToIndexMock).toHaveBeenCalledTimes(1)
  })

  it('pins array-local indexing even on a deep, non-zero-offset window', async () => {
    // Walk fix 9b round 4: a live fiber inspection suggested the index should be offset-adjusted
    // ("absolute") once firstItemIndex is non-zero. That was tried and empirically disproven — an
    // offset-adjusted index (260 here) drove virtuoso into a runaway endReached fetch loop instead
    // of landing on the target; the array-local index (60) landed correctly, one fetch, first try
    // (see round 4's report for the live A/B test). This pins the array-local contract directly
    // against a window that does NOT start at offset 0, so a future regression back toward
    // "absolute" fails here instead of only showing up against a live nonzero-offset deeplink.
    const offset = 200
    fetchMessages.mockResolvedValueOnce(pageOf(offset, 100, 500))
    renderView('uuid-260')
    await screen.findAllByTestId('row')

    const arrayIndex = 60 // uuid-260 is the 61st item in a page starting at uuid-200
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(arrayIndex)
    await waitFor(() =>
      expect(scrollToIndexMock).toHaveBeenCalledWith({
        index: arrayIndex,
        align: 'center',
        behavior: 'auto',
      }),
    )
  })
})

describe('startReached', () => {
  it('prepends the previous page and decrements firstItemIndex by the fetched count', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90')
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(0, 40, 250))
    fireEvent.click(screen.getByText('reach-start'))

    await waitFor(() => expect(virtuosoProps.current?.firstItemIndex).toBe(0))
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { offset: 0, limit: 40 })

    const rows = screen.getAllByTestId('row')
    expect(rows).toHaveLength(140)
    expect(rows[0].textContent).toContain('message 0')
    expect(rows[40].textContent).toContain('message 40')
  })

  it('is a no-op once the window already starts at 0', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView()
    await screen.findAllByTestId('row')

    fireEvent.click(screen.getByText('reach-start'))

    // Give any (wrong) fetch a chance to fire, then confirm only the initial load happened.
    await waitFor(() => expect(fetchMessages).toHaveBeenCalledTimes(1))
    expect(screen.getAllByTestId('row')).toHaveLength(100)
  })
})

describe('endReached', () => {
  it('appends the next page after the window', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 150))
    renderView()
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(100, 50, 150))
    fireEvent.click(screen.getByText('reach-end'))

    await waitFor(() => expect(screen.getAllByTestId('row')).toHaveLength(150))
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { offset: 100, limit: 100 })

    const rows = screen.getAllByTestId('row')
    expect(rows[99].textContent).toContain('message 99')
    expect(rows[100].textContent).toContain('message 100')
    expect(rows[149].textContent).toContain('message 149')
  })

  it('stops fetching once the window covers the archive total', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 150))
    renderView()
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(100, 50, 150))
    fireEvent.click(screen.getByText('reach-end'))
    await waitFor(() => expect(screen.getAllByTestId('row')).toHaveLength(150))

    fireEvent.click(screen.getByText('reach-end'))
    await waitFor(() => expect(fetchMessages).toHaveBeenCalledTimes(2))
  })
})

describe('calm states', () => {
  it('renders an inline offline message when the initial fetch fails (non-404)', async () => {
    // Persistent reject: a non-404 error retries (useMessages policy), so every attempt must fail
    // for the query to settle into the error state that renders the offline text.
    fetchMessages.mockRejectedValue(new Error('boom'))
    renderView()
    expect(await screen.findByText('archive offline')).toBeDefined()
  })

  it('renders a calm empty state for a transcript with no messages', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 0, 0))
    renderView()
    expect(await screen.findByText('Nothing recorded in this transcript.')).toBeDefined()
  })
})

describe('around-target not found (404)', () => {
  it('shows a not-found notice and recovers to offset 0 via "view from the beginning"', async () => {
    fetchMessages.mockRejectedValueOnce(new ApiError(404, 'Not Found', 'record uuid-x not found'))
    renderView('uuid-x')

    expect(await screen.findByText(/message not found in this conversation/)).toBeDefined()
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, { around: 'uuid-x', limit: 100 })

    // Recovery: dropping the around-seed re-fetches offset 0 and renders the window.
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    await userEvent.click(screen.getByRole('button', { name: 'view from the beginning' }))

    expect(await screen.findAllByTestId('row')).toHaveLength(100)
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { offset: 0, limit: 100 })
    expect(virtuosoProps.current?.firstItemIndex).toBe(0)
  })

  it('does not retry a 404 around-fetch (single call)', async () => {
    fetchMessages.mockRejectedValue(new ApiError(404, 'Not Found', 'nope'))
    renderView('uuid-x')

    expect(await screen.findByText(/message not found in this conversation/)).toBeDefined()
    // The 404-skipping retry policy means exactly one attempt, no storm.
    expect(fetchMessages).toHaveBeenCalledTimes(1)
  })

  it('keeps the offline text for a non-404 ApiError on an around-fetch', async () => {
    fetchMessages.mockRejectedValue(new ApiError(503, 'Service Unavailable', 'down'))
    renderView('uuid-x')
    expect(await screen.findByText('archive offline')).toBeDefined()
  })
})

// §9 amendment 2026-07-20: the /m/{uuid} deep-link target keeps a PERSISTENT marker (dawn accent +
// faint wash — class `deep-link-target`) on its data-record-uuid wrapper after the transient glow
// fades. Only the target carries it; "view from the beginning" (around dropped) clears it.
describe('persistent deep-link marker', () => {
  it('marks the around-target row and no others', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    const { container } = renderView('uuid-90')
    await screen.findAllByTestId('row')

    const target = container.querySelector('[data-record-uuid="uuid-90"]')
    expect(target?.classList.contains('deep-link-target')).toBe(true)

    const other = container.querySelector('[data-record-uuid="uuid-89"]')
    expect(other?.classList.contains('deep-link-target')).toBe(false)
  })

  it('carries no marker when there is no around target', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    const { container } = renderView()
    await screen.findAllByTestId('row')

    expect(container.querySelector('.deep-link-target')).toBeNull()
  })

  it('drops the marker after "view from the beginning" recovery (around dropped)', async () => {
    fetchMessages.mockRejectedValueOnce(new ApiError(404, 'Not Found', 'record uuid-x not found'))
    const { container } = renderView('uuid-x')
    await screen.findByText(/message not found in this conversation/)

    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    await userEvent.click(screen.getByRole('button', { name: 'view from the beginning' }))

    await screen.findAllByTestId('row')
    expect(container.querySelector('.deep-link-target')).toBeNull()
  })
})

// §14.4: chat_only must ride ALL THREE fetch sites — the seed, loadBefore, and loadAfter — so an
// unfiltered edge page can never splice into a filtered window and corrupt the offset math.
describe('chat_only threads through every fetch site', () => {
  it('seeds offset 0 with chat_only when active', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView(undefined, true)

    await screen.findAllByTestId('row')
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, {
      offset: 0,
      limit: 100,
      chat_only: true,
    })
  })

  it('seeds the around page with chat_only when active', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90', true)

    await screen.findAllByTestId('row')
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, {
      around: 'uuid-90',
      limit: 100,
      chat_only: true,
    })
  })

  it('carries chat_only into loadBefore (startReached) and loadAfter (endReached)', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90', true)
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(0, 40, 250))
    fireEvent.click(screen.getByText('reach-start'))
    await waitFor(() => expect(virtuosoProps.current?.firstItemIndex).toBe(0))
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, {
      offset: 0,
      limit: 40,
      chat_only: true,
    })

    fetchMessages.mockResolvedValueOnce(pageOf(140, 100, 250))
    fireEvent.click(screen.getByText('reach-end'))
    await waitFor(() =>
      expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, {
        offset: 140,
        limit: 100,
        chat_only: true,
      }),
    )
  })

  it('omits chat_only entirely from the opts object when inactive', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView(undefined, false)

    await screen.findAllByTestId('row')
    // Exactly the legacy opts shape — no chat_only key at all (server default is false).
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, { offset: 0, limit: 100 })
  })
})

// Top / End reader controls: quiet buttons that re-seed the window at offset 0 (top) or the last
// page (end). Both must thread chat_only through the re-seed fetch and work in the shared reader.
describe('top / end reader controls', () => {
  it('renders quiet top and end controls once the stream is loaded', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView()
    await screen.findAllByTestId('row')

    expect(screen.getByRole('button', { name: 'top' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'end' })).toBeDefined()
  })

  it('top re-seeds the window at offset 0 (dropping an around seed)', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90')
    await screen.findAllByTestId('row')
    expect(virtuosoProps.current?.firstItemIndex).toBe(40)
    // The around-seeded mount's own centering effect already fired once here — clear it so the
    // assertion below is scoped to the Top click's remount, not this earlier, correct call.
    scrollToIndexMock.mockClear()

    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    await userEvent.click(screen.getByRole('button', { name: 'top' }))

    await waitFor(() => expect(virtuosoProps.current?.firstItemIndex).toBe(0))
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { offset: 0, limit: 100 })
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(0)
    // No target to align to on the Top-reseeded (remounted) window — the one-shot effect is a
    // no-op there.
    expect(scrollToIndexMock).not.toHaveBeenCalled()
  })

  it('end fetches the LAST page (offset = total - PAGE_SIZE) and renders the final message', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView()
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(150, 100, 250))
    await userEvent.click(screen.getByRole('button', { name: 'end' }))

    await waitFor(() =>
      expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { offset: 150, limit: 100 }),
    )
    // Window seeded at the last page (firstItemIndex 150); array-local index of the last loaded
    // item is 99 (walk fix 9b round 4 confirmed this path is array-local too, not offset-adjusted
    // — see the NOTE at ConversationView.tsx's `targetIndex`).
    expect(virtuosoProps.current?.firstItemIndex).toBe(150)
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(99)
    const rows = screen.getAllByTestId('row')
    expect(rows[rows.length - 1].textContent).toContain('message 249')
    // The bottom-pin happens imperatively too, once, after mount.
    await waitFor(() =>
      expect(scrollToIndexMock).toHaveBeenCalledWith({ index: 99, align: 'end', behavior: 'auto' }),
    )
    expect(scrollToIndexMock).toHaveBeenCalledTimes(1)
  })

  it('threads chat_only through the end re-seed fetch', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 250))
    renderView(undefined, true)
    await screen.findAllByTestId('row')

    fetchMessages.mockResolvedValueOnce(pageOf(150, 100, 250))
    await userEvent.click(screen.getByRole('button', { name: 'end' }))

    await waitFor(() =>
      expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, {
        offset: 150,
        limit: 100,
        chat_only: true,
      }),
    )
  })

  it('top re-seeds an appended window back to just the first page (already-loaded)', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 100, 300))
    renderView()
    await screen.findAllByTestId('row')

    // Grow the window by appending the next page, then jump to top.
    fetchMessages.mockResolvedValueOnce(pageOf(100, 100, 300))
    fireEvent.click(screen.getByText('reach-end'))
    await waitFor(() => expect(screen.getAllByTestId('row')).toHaveLength(200))

    // A cheap re-seed: offset 0 is already cached, so no new fetch — the window remounts back to
    // the single first page at firstItemIndex 0.
    await userEvent.click(screen.getByRole('button', { name: 'top' }))
    await waitFor(() => expect(screen.getAllByTestId('row')).toHaveLength(100))
    expect(virtuosoProps.current?.firstItemIndex).toBe(0)
  })
})

// §15.2: each row's speaker-name button opens the reader-level raw-record inspector, seeded on
// that row's uuid and reading the loaded window as its traversal order (§5/§6 rework: the name
// replaced the retired `{}` as the trigger). This is the wiring check; the modal's own behavior
// matrix lives in RawRecordInspector.test.tsx.
describe('raw-record inspector wiring', () => {
  it('opens the inspector on the clicked row, seeded with that row’s uuid', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(0, 3, 3))
    fetchRawRecord.mockResolvedValue('{"hello":"world"}')
    renderView()
    await screen.findAllByTestId('row')

    const inspectButtons = screen.getAllByRole('button', { name: /view raw record/ })
    await userEvent.click(inspectButtons[0])

    expect(await screen.findByRole('dialog')).toBeDefined()
    await waitFor(() => expect(fetchRawRecord).toHaveBeenCalledWith('uuid-0'))
  })
})

// Critique #12: the around-404 notice gains a SECOND action under chat_only — "show all message
// types" clears the toggle while KEEPING the same around= seed, so the target re-resolves in the
// unfiltered set (distinct from "view from the beginning", which drops the around seed).
describe('around-404 recovery under chat_only (critique #12)', () => {
  it('offers "show all message types" only when chatOnly is active', async () => {
    fetchMessages.mockRejectedValueOnce(new ApiError(404, 'Not Found', 'filtered out'))
    renderView('uuid-x', true)

    expect(await screen.findByText(/message not found in this conversation/)).toBeDefined()
    expect(screen.getByRole('button', { name: 'view from the beginning' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'show all message types' })).toBeDefined()
  })

  it('hides "show all message types" when chatOnly is off (meaningless there)', async () => {
    fetchMessages.mockRejectedValueOnce(new ApiError(404, 'Not Found', 'nope'))
    renderView('uuid-x', false)

    expect(await screen.findByText(/message not found in this conversation/)).toBeDefined()
    expect(screen.queryByRole('button', { name: 'show all message types' })).toBeNull()
    expect(screen.getByRole('button', { name: 'view from the beginning' })).toBeDefined()
  })

  it('"show all message types" calls setChatOnly(false)', async () => {
    const setChatOnly = vi.fn()
    fetchMessages.mockRejectedValueOnce(new ApiError(404, 'Not Found', 'filtered out'))
    renderView('uuid-x', true, setChatOnly)

    await screen.findByText(/message not found in this conversation/)
    await userEvent.click(screen.getByRole('button', { name: 'show all message types' }))
    expect(setChatOnly).toHaveBeenCalledWith(false)
  })

  it('full recovery: clearing the toggle re-resolves the SAME around target unfiltered', async () => {
    // The trap-proof sequence: 404 under chat_only, 200 without — and the around seed is KEPT.
    fetchMessages.mockImplementation((_id: number, opts: MessagesOptions) =>
      opts.chat_only
        ? Promise.reject(new ApiError(404, 'Not Found', 'filtered out'))
        : Promise.resolve(pageOf(40, 100, 250)),
    )

    function Harness() {
      const [chatOnly, setChatOnly] = useState(true)
      return (
        <ConversationView
          transcriptId={TRANSCRIPT_ID}
          initialAroundUuid="uuid-90"
          chatOnly={chatOnly}
          setChatOnly={setChatOnly}
        />
      )
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
    render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )

    await screen.findByText(/message not found in this conversation/)
    await userEvent.click(screen.getByRole('button', { name: 'show all message types' }))

    expect(await screen.findAllByTestId('row')).toHaveLength(100)
    // Same around seed, chat_only dropped — the target is found in the unfiltered set.
    expect(fetchMessages).toHaveBeenLastCalledWith(TRANSCRIPT_ID, { around: 'uuid-90', limit: 100 })
  })
})
