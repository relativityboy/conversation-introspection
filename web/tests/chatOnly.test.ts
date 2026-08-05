import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { isChatOnlyVisible, useChatOnly, type UseChatOnly } from '../src/lib/chatOnly'

// The single localStorage key that makes the toggle sticky across sessions and readers.
const KEY = 'introspect.chatOnly.v1'

// PARITY PIN: mirrors server/tests/test_api_sessions.py::test_chat_only_trims_content_empty_rows
// — change both together. One rule, two implementations (spec §4).
const text = (s: string | null) => ({ block_kind: 'text', text_content: s })
const kind = (k: string) => ({ block_kind: k, text_content: null })

const CASES: Array<
  [string, { type: string; blocks: { block_kind: string; text_content: string | null }[] }, boolean]
> = [
  ['assistant, tool blocks only', { type: 'assistant', blocks: [kind('tool_use'), kind('tool_result')] }, false],
  ['assistant, thinking only (◌)', { type: 'assistant', blocks: [kind('thinking')] }, false],
  ['user, empty text', { type: 'user', blocks: [text('')] }, false],
  ['user, null text', { type: 'user', blocks: [text(null)] }, false],
  ['assistant, unknown kind', { type: 'assistant', blocks: [kind('futurekind')] }, true],
  ['assistant, real text', { type: 'assistant', blocks: [text('hello')] }, true],
  ['assistant, image only', { type: 'assistant', blocks: [kind('image')] }, true],
  ['attachment, zero blocks (furniture)', { type: 'attachment', blocks: [] }, false],
  ['attachment, rescued prompt', { type: 'attachment', blocks: [text('queued words')] }, true],
  ['system, real text (type-excluded)', { type: 'system', blocks: [text('x')] }, false],
  ['assistant, zero blocks', { type: 'assistant', blocks: [] }, false],
]

describe('isChatOnlyVisible — trim rule parity', () => {
  it.each(CASES)('%s', (_name, message, visible) => {
    expect(isChatOnlyVisible(message)).toBe(visible)
  })
})

beforeEach(() => {
  window.localStorage.clear()
})

describe('useChatOnly', () => {
  it('defaults to off when nothing is stored', () => {
    const { result } = renderHook(() => useChatOnly())
    expect(result.current[0]).toBe(false)
  })

  it('turns on and persists the flag to localStorage', () => {
    const { result } = renderHook(() => useChatOnly())
    act(() => result.current[1](true))
    expect(result.current[0]).toBe(true)
    expect(window.localStorage.getItem(KEY)).toBe('1')
  })

  it('seeds ON from a pre-existing stored flag — sticky across mounts', () => {
    window.localStorage.setItem(KEY, '1')
    const { result } = renderHook(() => useChatOnly())
    expect(result.current[0]).toBe(true)
  })

  it('clears the key when turned back off (no stale "0" left behind)', () => {
    window.localStorage.setItem(KEY, '1')
    const { result } = renderHook(() => useChatOnly())
    act(() => result.current[1](false))
    expect(result.current[0]).toBe(false)
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('degrades to in-memory state when localStorage.setItem throws (private mode)', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    const { result } = renderHook(() => useChatOnly())
    expect(() => act(() => result.current[1](true))).not.toThrow()
    // The write threw, but the in-memory state must still flip so the UI stays responsive.
    expect(result.current[0]).toBe(true)
    spy.mockRestore()
  })

  it('reads as off without throwing when localStorage.getItem throws (private mode)', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })
    let rendered: ReturnType<typeof renderHook<UseChatOnly, unknown>> | undefined
    expect(() => {
      rendered = renderHook(() => useChatOnly())
    }).not.toThrow()
    expect(rendered?.result.current[0]).toBe(false)
    spy.mockRestore()
  })
})
