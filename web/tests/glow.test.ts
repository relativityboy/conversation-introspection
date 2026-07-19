import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyGlow } from '../src/lib/glow'

const GLOW_CLASS = 'deep-link-glow'

// jsdom does not implement scrollIntoView; applyGlow guards it, but the tests give the element a
// spy so the "scroll always happens" contract is observable independent of the glow class.
function makeElement(): HTMLElement {
  const el = document.createElement('div')
  el.scrollIntoView = vi.fn()
  return el
}

// setup.ts installs a matches:false matchMedia in a global beforeEach; the reduced-motion test
// overrides it. Fake timers isolate the 2.5s fallback from the animationend path.
beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('applyGlow', () => {
  it('adds the glow class and scrolls the target into view', () => {
    const el = makeElement()
    applyGlow(el)

    expect(el.classList.contains(GLOW_CLASS)).toBe(true)
    expect(el.scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('removes the class when the CSS animation ends', () => {
    const el = makeElement()
    applyGlow(el)
    expect(el.classList.contains(GLOW_CLASS)).toBe(true)

    el.dispatchEvent(new Event('animationend'))

    expect(el.classList.contains(GLOW_CLASS)).toBe(false)
  })

  it('removes the class via the timeout fallback when no animation runs', () => {
    const el = makeElement()
    applyGlow(el)
    expect(el.classList.contains(GLOW_CLASS)).toBe(true)

    // Environments that never fire animationend (jsdom, and browsers that don't run the
    // animation) rely on this fallback; 2.5s > the 2s CSS fade.
    vi.advanceTimersByTime(2500)

    expect(el.classList.contains(GLOW_CLASS)).toBe(false)
  })

  it('under prefers-reduced-motion, scrolls but NEVER adds the glow class', () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))

    const el = makeElement()
    applyGlow(el)

    expect(el.classList.contains(GLOW_CLASS)).toBe(false)
    expect(el.scrollIntoView).toHaveBeenCalledTimes(1)

    // And it stays absent — no deferred add is scheduled.
    vi.advanceTimersByTime(3000)
    expect(el.classList.contains(GLOW_CLASS)).toBe(false)
  })
})
