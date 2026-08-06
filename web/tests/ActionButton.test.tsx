import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ActionButton } from '../src/components/ActionButton'

// userEvent doesn't play well with fake timers for this machine; fireEvent-style clicks via
// button.click() inside act() are what these tests need.
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('ActionButton', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('success: pending (aria-busy, clicks ignored) → default flash → idle after 2s', async () => {
    const d = deferred<void>()
    const onClick = vi.fn(() => d.promise)
    render(<ActionButton glyph="⟲" text="resume" onClick={onClick} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn.getAttribute('aria-busy')).toBe('true')
    await act(async () => btn.click()) // ignored while pending
    expect(onClick).toHaveBeenCalledTimes(1)
    await act(async () => d.resolve())
    expect(btn.textContent).toContain('resume ✓')
    expect(btn.className).toContain('is-success')
    await act(async () => vi.advanceTimersByTime(2000))
    expect(btn.textContent).toContain('resume')
    expect(btn.className).not.toContain('is-success')
  })

  it('a resolved string overrides the flash label', async () => {
    render(<ActionButton glyph="●" text="import" onClick={async () => 'already running'} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn.textContent).toContain('already running')
    expect(btn.className).toContain('is-success')
  })

  it('error: sticky with message in title; click 1 dismisses without re-firing; click 2 retries', async () => {
    const onClick = vi.fn().mockRejectedValueOnce(new Error('no terminal')).mockResolvedValue(undefined)
    render(<ActionButton glyph="⟲" text="resume" onClick={onClick} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn.textContent).toContain('⚠ resume failed')
    expect(btn.getAttribute('title')).toBe('no terminal')
    await act(async () => vi.advanceTimersByTime(5000)) // sticky, not a flash
    expect(btn.textContent).toContain('⚠ resume failed')
    await act(async () => btn.click()) // dismiss
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(btn.textContent).toContain('resume')
    await act(async () => btn.click()) // retry
    expect(onClick).toHaveBeenCalledTimes(2)
  })

  it('error path schedules NO flash timer, and unmount clears the success timer', async () => {
    const d1 = deferred<void>()
    const { unmount, rerender } = render(<ActionButton glyph="●" text="import" onClick={() => d1.promise} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    await act(async () => d1.reject(new Error('boom')))
    expect(vi.getTimerCount()).toBe(0) // upstream bug not ported: no stray setTimeout after error
    await act(async () => btn.click()) // dismiss
    const d2 = deferred<void>()
    rerender(<ActionButton glyph="●" text="import" onClick={() => d2.promise} />)
    await act(async () => btn.click())
    await act(async () => d2.resolve())
    expect(vi.getTimerCount()).toBe(1) // flash timer armed
    unmount()
    expect(vi.getTimerCount()).toBe(0) // cleared on unmount — no post-unmount state write
  })

  it('resolving after unmount does not throw or write state', async () => {
    const d = deferred<void>()
    const { unmount } = render(<ActionButton glyph="⟲" text="resume" onClick={() => d.promise} />)
    await act(async () => screen.getByRole('button').click())
    unmount()
    await act(async () => d.resolve()) // must be a silent no-op
    expect(vi.getTimerCount()).toBe(0)
  })

  // final review fix: consumers (StatusBar's GHOST_BTN, ActionsMenu's ITEM_STYLE) pass an inline
  // style.color of their own, which — as a later-applied inline style — beat the CSS class rules
  // for is-success/is-error, so the phase colors never actually rendered. ActionButton now merges
  // its own phase color over the consumer's style when phase !== idle.
  describe('phase color wins over consumer style.color', () => {
    it('idle keeps the consumer style color untouched', () => {
      render(
        <ActionButton glyph="●" text="import" onClick={vi.fn()} style={{ color: 'var(--mist)' }} />,
      )
      expect(screen.getByRole('button').style.color).toBe('var(--mist)')
    })

    it('success overrides the consumer style color with dragonfly', async () => {
      const onClick = vi.fn().mockResolvedValue(undefined)
      render(
        <ActionButton glyph="●" text="import" onClick={onClick} style={{ color: 'var(--mist)' }} />,
      )
      const btn = screen.getByRole('button')
      await act(async () => btn.click())
      expect(btn.style.color).toBe('var(--dragonfly)')
    })

    it('error overrides the consumer style color with ember', async () => {
      const onClick = vi.fn().mockRejectedValueOnce(new Error('boom'))
      render(
        <ActionButton glyph="⟲" text="resume" onClick={onClick} style={{ color: 'var(--dragonfly)' }} />,
      )
      const btn = screen.getByRole('button')
      await act(async () => btn.click())
      expect(btn.style.color).toBe('var(--ember)')
    })
  })
})
