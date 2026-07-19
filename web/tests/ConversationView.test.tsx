import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import type { MessageList, MessageOut } from '../src/api/types'
import { ConversationView } from '../src/components/reader/ConversationView'

// Windowing is tested at the LOGIC level per the task contract: jsdom has no layout engine, so
// real react-virtuoso can never honestly fire startReached/endReached from scrolling. The mock
// captures the props ConversationView hands to Virtuoso (firstItemIndex, totalCount,
// initialTopMostItemIndex) and exposes the two edge callbacks as buttons; every row the real
// component would virtualize is rendered flat so prepend/append order is observable in the DOM.

interface VirtuosoMockProps {
  totalCount: number
  firstItemIndex: number
  initialTopMostItemIndex?: number
  startReached?: (index: number) => void
  endReached?: (index: number) => void
  itemContent: (index: number) => ReactNode
}

const { fetchMessages, virtuosoProps } = vi.hoisted(() => ({
  fetchMessages: vi.fn(),
  virtuosoProps: { current: null as VirtuosoMockProps | null },
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchMessages }
})

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: VirtuosoMockProps) => {
    virtuosoProps.current = props
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

function renderView(initialAroundUuid?: string) {
  // We deliberately DON'T disable retry here: useMessages owns the retry policy (skip 404s,
  // else the default 3), and the 404/offline cases below exist to exercise exactly that.
  // retryDelay 0 keeps the retrying (non-404) case instant instead of backing off for seconds.
  const queryClient = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ConversationView transcriptId={TRANSCRIPT_ID} initialAroundUuid={initialAroundUuid} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  fetchMessages.mockReset()
  virtuosoProps.current = null
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
  it('seeds firstItemIndex from the response offset and starts atop the target uuid', async () => {
    fetchMessages.mockResolvedValueOnce(pageOf(40, 100, 250))
    renderView('uuid-90')

    expect(await screen.findAllByTestId('row')).toHaveLength(100)
    expect(fetchMessages).toHaveBeenCalledWith(TRANSCRIPT_ID, { around: 'uuid-90', limit: 100 })
    expect(virtuosoProps.current?.firstItemIndex).toBe(40)
    // uuid-90 sits at array index 50 within the seeded page (items are uuid-40..uuid-139).
    expect(virtuosoProps.current?.initialTopMostItemIndex).toBe(50)
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
