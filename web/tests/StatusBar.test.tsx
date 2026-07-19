import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { useImportRun } from '../src/api/hooks'
import { StatusBar } from '../src/components/StatusBar'
import type { ImportRun, StatusOut } from '../src/api/types'

// Same convention as the rest of the suite (Sidebar/search/ConversationView tests): mock the api
// client module — hooks.ts imports these named functions directly, so this swaps the network
// layer for useStatus/useTriggerImport/useImportRun in one place.
const { fetchStatus, triggerImport, fetchImportRun } = vi.hoisted(() => ({
  fetchStatus: vi.fn(),
  triggerImport: vi.fn(),
  fetchImportRun: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchStatus, triggerImport, fetchImportRun }
})

beforeEach(() => {
  fetchStatus.mockReset()
  triggerImport.mockReset()
  fetchImportRun.mockReset()
})

// --- fixtures -----------------------------------------------------------------------------

function makeImportRun(over: Partial<ImportRun> = {}): ImportRun {
  return {
    id: 1,
    trigger: 'api',
    status: 'running',
    started_at: new Date().toISOString(),
    finished_at: null,
    files_seen: 0,
    records_added: 0,
    records_skipped_duplicate: 0,
    anomaly_count: 0,
    ...over,
  }
}

function makeStatus(over: Partial<StatusOut> = {}): StatusOut {
  return {
    sessions: 5,
    files: 143,
    records: 14312,
    archive_bytes: 47_200_000, // decimal MB → exactly "47.2 MB", matching the design mockup
    anomalies: { error: 0, warn: 2, info: 10 },
    last_run: makeImportRun({
      status: 'ok',
      // 15 real minutes before "now" — computed at fixture time (no system-clock mocking
      // needed) so relativeTime's floor(minutes) reads exactly 15 as long as the test finishes
      // inside the same minute, which a synchronous render comfortably does.
      started_at: new Date(Date.now() - 15 * 60_000 - 5_000).toISOString(),
      finished_at: new Date(Date.now() - 15 * 60_000).toISOString(),
    }),
    ...over,
  }
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <StatusBar />
    </QueryClientProvider>,
  )
  return { queryClient, ...utils }
}

// --- renders status -----------------------------------------------------------------------

describe('renders status', () => {
  it('shows last-import relative time, record/file counts, anomaly total, and archive MB', async () => {
    fetchStatus.mockResolvedValueOnce(makeStatus())
    setup()

    expect(
      await screen.findByText('last import 15m ago · 14312 records · 143 files'),
    ).toBeDefined()
    const anomalies = screen.getByText('12 anomalies') // error(0) + warn(2) + info(10)
    expect(anomalies.style.color).toBe('var(--mist)')
    expect(screen.getByText('archive: 47.2 MB')).toBeDefined()
  })

  it('colors the anomaly count ember when anomalies.error > 0', async () => {
    fetchStatus.mockResolvedValueOnce(makeStatus({ anomalies: { error: 3, warn: 1, info: 0 } }))
    setup()

    const anomalies = await screen.findByText('4 anomalies')
    expect(anomalies.style.color).toBe('var(--ember)')
  })
})

// --- import trigger -------------------------------------------------------------------------

describe('import trigger', () => {
  it('calls the trigger mutation and shows "importing…" while the run polls running', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValueOnce({ run_id: 7 })
    fetchImportRun.mockResolvedValue(makeImportRun({ id: 7, status: 'running' }))
    setup()

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))

    expect(await screen.findByText('importing…')).toBeDefined()
    expect(triggerImport).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(fetchImportRun).toHaveBeenCalledWith(7))
  })
})

// --- terminal run states --------------------------------------------------------------------

describe('terminal run states', () => {
  it('shows "imported ✓" and invalidates status+sessions when the run finishes ok', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValueOnce({ run_id: 8 })
    fetchImportRun.mockResolvedValueOnce(makeImportRun({ id: 8, status: 'ok' }))
    const { queryClient } = setup()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))

    expect(await screen.findByText('imported ✓')).toBeDefined()
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
  })

  it('shows "import failed" (ember) when the run finishes in an error status', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValueOnce({ run_id: 9 })
    fetchImportRun.mockResolvedValueOnce(makeImportRun({ id: 9, status: 'errors' }))
    setup()

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))

    const failed = await screen.findByText('import failed')
    expect(failed.style.color).toBe('var(--ember)')
  })

  it('shows "already running" on a 409 from the trigger mutation, without polling any run', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockRejectedValueOnce(new ApiError(409, 'import already running', 'conflict'))
    setup()

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))

    expect(await screen.findByText('already running')).toBeDefined()
    expect(fetchImportRun).not.toHaveBeenCalled()
  })
})

// --- poll failure is terminal, never a wedge --------------------------------------------------
// The regression these pin: the phase machine treated ONLY a terminal run status as an exit from
// 'running'. A failed poll (data undefined forever, or stale 'running' data + isError) left the
// bar stuck on "importing…" with the button hidden and no recovery.

describe('poll failure recovery', () => {
  // shouldAdvanceTime keeps testing-library's own setTimeout-based waitFor polling alive while
  // still letting the test fast-forward the 1s refetch interval and the 4s message revert —
  // same convention as Sidebar.test.tsx's debounce suite.
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('recovers when the FIRST poll fails: "import failed", then the button returns', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValueOnce({ run_id: 10 })
    fetchImportRun.mockRejectedValue(new Error('boom'))
    setup()

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))

    const failed = await screen.findByText('import failed')
    expect(failed.style.color).toBe('var(--ember)')

    // Past the 4s transient-message window the machine must be back at idle: button restored.
    await vi.advanceTimersByTimeAsync(4100)
    expect(await screen.findByRole('button', { name: '● import' })).toBeDefined()
  })

  it('recovers when the server dies MID-run (stale running data + error) and stops polling', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValueOnce({ run_id: 11 })
    fetchImportRun
      .mockResolvedValueOnce(makeImportRun({ id: 11, status: 'running' }))
      .mockRejectedValue(new Error('server gone'))
    setup()

    fireEvent.click(await screen.findByRole('button', { name: '● import' }))
    expect(await screen.findByText('importing…')).toBeDefined()

    // Advance past the 1s refetch interval so the second (failing) poll fires. react-query keeps
    // the STALE data (status 'running') — isError is what must flip the machine to 'error'.
    await vi.advanceTimersByTimeAsync(1100)
    expect(await screen.findByText('import failed')).toBeDefined()

    // Polling must stop dead: the call count stabilizes across several more would-be intervals.
    const settledCalls = fetchImportRun.mock.calls.length
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchImportRun.mock.calls.length).toBe(settledCalls)
  })

  it('useImportRun itself stops the 1s interval once the query errors (stale running data)', async () => {
    // Hook-level pin, independent of StatusBar's phase gating: even a consumer that keeps the id
    // non-null forever must not poll a dead server every 1s off the stale 'running' data.
    fetchImportRun
      .mockResolvedValueOnce(makeImportRun({ id: 5, status: 'running' }))
      .mockRejectedValue(new Error('gone'))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderHook(() => useImportRun(5), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    })

    await waitFor(() => expect(fetchImportRun).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(1100) // the failing second poll
    await waitFor(() => expect(fetchImportRun).toHaveBeenCalledTimes(2))

    await vi.advanceTimersByTimeAsync(3000) // three more would-be intervals
    expect(fetchImportRun).toHaveBeenCalledTimes(2)
  })
})

// --- status error ---------------------------------------------------------------------------

describe('status error', () => {
  it('renders a single calm "archive offline" line and nothing else', async () => {
    fetchStatus.mockRejectedValueOnce(new Error('boom'))
    setup()

    expect(await screen.findByText('archive offline')).toBeDefined()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText(/records/)).toBeNull()
    expect(screen.queryByText(/anomalies/)).toBeNull()
    expect(screen.queryByText(/archive:/)).toBeNull()
  })
})
