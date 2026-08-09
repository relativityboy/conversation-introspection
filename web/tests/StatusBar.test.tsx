import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../src/api/client'
import { StatusBar } from '../src/components/StatusBar'
import type { ImportRun, StatusOut } from '../src/api/types'

// Same convention as the rest of the suite (Sidebar/search/ConversationView tests): mock the api
// client module — StatusBar now imports `triggerImport`/`fetchImportRun` directly (Task 2 folded
// the old useTriggerImport/useImportRun hooks into StatusBar's own `runImport`), and useStatus
// still goes through hooks.ts -> client.ts, so mocking the client module covers all three.
const { fetchStatus, triggerImport, fetchImportRun } = vi.hoisted(() => ({
  fetchStatus: vi.fn(),
  triggerImport: vi.fn(),
  fetchImportRun: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchStatus, triggerImport, fetchImportRun }
})

// Version chip tests pin the UI's own version, not the build-time baked value (which is
// 'unknown' outside a real vite build) — see web/src/version.ts's typeof guard.
vi.mock('../src/version', () => ({ UI_VERSION: '1.2.0' }))

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
    version: '1.2.0',
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

// --- version chip -----------------------------------------------------------------------
// UI_VERSION is mocked to '1.2.0' above; these three pin the agree/differ/unknown display
// rules from spec §3.

describe('version chip', () => {
  it('shows a single version chip when ui and server agree', async () => {
    fetchStatus.mockResolvedValue(makeStatus({ version: '1.2.0' }))
    setup()
    expect(await screen.findByText('v1.2.0')).toBeDefined()
  })

  it('shows both versions when they differ', async () => {
    fetchStatus.mockResolvedValue(makeStatus({ version: '1.3.0' }))
    setup()
    expect(await screen.findByText('ui v1.2.0 · server v1.3.0')).toBeDefined()
  })

  it('omits the chip when the server version is unknown', async () => {
    fetchStatus.mockResolvedValue(makeStatus({ version: 'unknown' }))
    setup()
    await screen.findByText(/last import/) // bar rendered
    expect(screen.queryByText(/^v|ui v/)).toBeNull()
  })
})

// --- import trigger (ActionButton's onClick is StatusBar's `runImport`) --------------------
// The Phase machine is gone: ActionButton owns pending/success/error UI on its own, driven
// entirely by what `runImport` resolves or throws. These four cases pin that contract (task
// brief Step 1).

describe('import trigger', () => {
  beforeEach(() => {
    // shouldAdvanceTime keeps testing-library's own setTimeout-based findBy/waitFor polling
    // alive while still letting the test fast-forward the 1s poll interval — same convention as
    // the old poll-failure-recovery suite this replaces.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('ok run: pending during poll, then "imported ✓" flash + status/sessions/projects invalidated', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValue({ run_id: 7 })
    fetchImportRun
      .mockResolvedValueOnce(makeImportRun({ status: 'running' }))
      .mockResolvedValueOnce(makeImportRun({ status: 'ok' }))
    const { queryClient } = setup()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const btn = await screen.findByRole('button', { name: '● import' })
    fireEvent.click(btn)

    expect(btn.getAttribute('aria-busy')).toBe('true')

    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(1000)

    expect(btn.textContent).toContain('imported ✓')
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['projects'] })
  })

  it('409 on trigger: neutral "already running" flash, NO invalidations', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockRejectedValue(new ApiError(409, 'Conflict', 'import already running'))
    const { queryClient } = setup()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const btn = await screen.findByRole('button', { name: '● import' })
    fireEvent.click(btn)

    await waitFor(() => expect(btn.textContent).toContain('already running'))
    expect(btn.className).toContain('is-success') // neutral flash, not the error styling
    expect(fetchImportRun).not.toHaveBeenCalled()
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('failed run: sticky "⚠ import failed", invalidations still fire', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValue({ run_id: 8 })
    fetchImportRun.mockResolvedValue(makeImportRun({ status: 'error' }))
    const { queryClient } = setup()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const btn = await screen.findByRole('button', { name: '● import' })
    fireEvent.click(btn)

    await waitFor(() => expect(btn.textContent).toContain('⚠ import failed'))
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['projects'] })

    // sticky: unlike a success flash, it must NOT auto-clear as time passes.
    await vi.advanceTimersByTimeAsync(5000)
    expect(btn.textContent).toContain('⚠ import failed')
  })

  it('poll network error: sticky error, invalidations still fire', async () => {
    fetchStatus.mockResolvedValue(makeStatus())
    triggerImport.mockResolvedValue({ run_id: 9 })
    fetchImportRun.mockRejectedValue(new Error('down'))
    const { queryClient } = setup()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const btn = await screen.findByRole('button', { name: '● import' })
    fireEvent.click(btn)

    await waitFor(() => expect(btn.textContent).toContain('⚠ import failed'))
    // The raw poll error ('down') must NOT leak into the button's title/aria-label — it has to
    // be normalized to the exact same 'import failed' message a run-status failure produces.
    expect(btn.getAttribute('title')).toBe('import failed')
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['status'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['projects'] })
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
