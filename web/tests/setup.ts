import { beforeEach, vi } from 'vitest'

// jsdom implements no layout engine, so it never provides ResizeObserver.
// react-virtuoso (Phase 3 message list) uses it to measure item heights; without
// this stub every virtualized list silently renders zero items in tests.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

// jsdom always reports 0 for these layout measurements. Fixed, non-zero values
// let virtualized components believe they have a viewport to render items into.
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get: () => 800,
})
Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
  configurable: true,
  get: () => 24,
})

// jsdom does not implement matchMedia at all. Default to "not matching" (e.g.
// prefers-reduced-motion reads as off); a test that needs a different answer
// can reassign window.matchMedia itself before rendering.
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})
