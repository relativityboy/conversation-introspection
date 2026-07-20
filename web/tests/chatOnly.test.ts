import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatOnly, type UseChatOnly } from '../src/lib/chatOnly'

// The single localStorage key that makes the toggle sticky across sessions and readers.
const KEY = 'introspect.chatOnly.v1'

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
