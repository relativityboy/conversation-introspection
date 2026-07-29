import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useSidebarTree } from '../src/lib/sidebarTree'

const KEY = 'introspect.sidebarTree.v1'

describe('useSidebarTree', () => {
  afterEach(() => window.localStorage.removeItem(KEY))

  it('defaults to flat (false) when the key is absent', () => {
    const { result } = renderHook(() => useSidebarTree())
    expect(result.current[0]).toBe(false)
  })

  it('seeds true from a stored "1"', () => {
    window.localStorage.setItem(KEY, '1')
    const { result } = renderHook(() => useSidebarTree())
    expect(result.current[0]).toBe(true)
  })

  it('setting true writes "1"; setting false REMOVES the key (absent === off)', () => {
    const { result } = renderHook(() => useSidebarTree())
    act(() => result.current[1](true))
    expect(window.localStorage.getItem(KEY)).toBe('1')
    act(() => result.current[1](false))
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})
