import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TopbarSearch } from '../src/components/TopbarSearch'

// writeSidebarParams is spied (real implementation preserved) so the debounce test can count URL
// WRITES, not just renders: react-router's setSearchParams is referentially unstable, and the
// echo regression it caused (a second identical write ~250ms after the first) is invisible to
// anything short of counting actual writes. Ported verbatim from Sidebar.test.tsx (:29-38) — this
// is the same spy pattern, now guarding TopbarSearch since it owns every ?filter= write.
const writeSidebarParamsSpy = vi.hoisted(() => vi.fn())

vi.mock('../src/lib/urlState', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/urlState')>('../src/lib/urlState')
  writeSidebarParamsSpy.mockImplementation(actual.writeSidebarParams)
  return { ...actual, writeSidebarParams: writeSidebarParamsSpy }
})

const DEBOUNCE_MS = 250

function renderTopbarSearch(initialEntries: string[] = ['/']) {
  const locationRef: { current: { search: string } | null } = { current: null }

  function LocationProbe() {
    const location = useLocation()
    locationRef.current = { search: location.search }
    return null
  }

  const utils = render(
    <MemoryRouter initialEntries={initialEntries}>
      <TopbarSearch />
      <LocationProbe />
    </MemoryRouter>,
  )

  return { locationRef, ...utils }
}

beforeEach(() => {
  // mockClear, NOT mockReset — reset would wipe the real implementation installed by the
  // module-mock factory above.
  writeSidebarParamsSpy.mockClear()
})

describe('content filter debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('writes the URL exactly once, with the final filter, after typing settles', async () => {
    const { locationRef } = renderTopbarSearch()
    writeSidebarParamsSpy.mockClear()

    const input = screen.getByPlaceholderText('Filter by title or content…')
    fireEvent.change(input, { target: { value: 'a' } })
    fireEvent.change(input, { target: { value: 'ab' } })
    fireEvent.change(input, { target: { value: 'abc' } })

    // No write yet — each keystroke should have reset the debounce timer, not fired one.
    expect(writeSidebarParamsSpy).not.toHaveBeenCalled()

    // Advance well past 2x the debounce window: the echo regression (setSearchParams's unstable
    // identity re-triggering the effect after its own write) produced a SECOND identical write at
    // ~500ms, which a single 250ms advance could never observe.
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 4)

    expect(writeSidebarParamsSpy).toHaveBeenCalledTimes(1)
    expect(writeSidebarParamsSpy).toHaveBeenCalledWith(expect.anything(), { filter: 'abc' })
    expect(locationRef.current?.search).toBe('?filter=abc')
  })

  it('restores the content filter from the URL on mount without waiting for the debounce, and does not write on mount', async () => {
    renderTopbarSearch(['/?filter=zzz'])

    const input = screen.getByPlaceholderText('Filter by title or content…') as HTMLInputElement
    expect(input.value).toBe('zzz')

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 4)
    expect(writeSidebarParamsSpy).not.toHaveBeenCalled()
  })

  // Zero-legacy ruling (relativityboy, ledger #4): the retired `?title=` key must NOT seed the
  // input. A deep link built against the old contract lands on an unfiltered box, not a silently
  // resurrected filter.
  it('does not seed the input from the retired `?title=` param', () => {
    renderTopbarSearch(['/?title=zzz'])

    const input = screen.getByPlaceholderText('Filter by title or content…') as HTMLInputElement
    expect(input.value).toBe('')
  })

  it('clearing the input writes a filter-delete', async () => {
    const { locationRef } = renderTopbarSearch(['/?filter=zzz'])
    writeSidebarParamsSpy.mockClear()

    const input = screen.getByPlaceholderText('Filter by title or content…')
    fireEvent.change(input, { target: { value: '' } })

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 4)

    expect(writeSidebarParamsSpy).toHaveBeenCalledTimes(1)
    expect(writeSidebarParamsSpy).toHaveBeenCalledWith(expect.anything(), { filter: '' })
    expect(locationRef.current?.search).toBe('')
  })
})
