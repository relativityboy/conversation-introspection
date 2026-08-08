import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlockOut, MessageOut } from '../src/api/types'
import { CHAT_KINDS, isVisibleInView, useViewMode } from '../src/lib/viewMode'

// The two localStorage keys in play: the new three-state key this hook owns, and the retired
// boolean key (Task P4-4/5) it must never resurrect a reading from.
const KEY = 'introspect.view.v1'
const LEGACY_KEY = 'introspect.chatOnly.v1'

function textBlock(text: string | null): BlockOut {
  return { block_index: 0, block_kind: 'text', text_content: text, tool_name: null, tool_use_id: null, is_error: null }
}

function kindBlock(kind: string): BlockOut {
  return { block_index: 0, block_kind: kind, text_content: null, tool_name: null, tool_use_id: null, is_error: null }
}

function message(over: Partial<MessageOut> = {}): MessageOut {
  return {
    record_uuid: 'rec-1',
    parent_uuid: null,
    type: 'user',
    model: null,
    timestamp: null,
    authorship_kind: null,
    authorship_basis: null,
    authorship_detail: null,
    blocks: [textBlock('hello')],
    ...over,
  }
}

describe('CHAT_KINDS', () => {
  it('mirrors the server set (schema/authorship.py)', () => {
    expect([...CHAT_KINDS].sort()).toEqual(
      [
        'human_typed',
        'human_queued',
        'human_inferred',
        'claude',
        'attachment_queued_human',
        'interrupt_marker',
        'dispatch',
        'coordinator',
      ].sort(),
    )
  })
})

// PARITY PIN: mirrors server/tests/test_api_sessions.py's view= three-way authorship filtering
// (Task 4, spec §5) — change both together. One rule, two implementations.
describe('isVisibleInView — authorship-kind parity', () => {
  it('interrupt_marker is visible in chat; skill_injection only in chat-harness/all; tool_result only in all', () => {
    const interrupt = message({
      type: 'user',
      authorship_kind: 'interrupt_marker',
      blocks: [textBlock('[Request interrupted by user]')],
    })
    expect(isVisibleInView(interrupt, 'chat')).toBe(true)
    expect(isVisibleInView(interrupt, 'chat-harness')).toBe(true)
    expect(isVisibleInView(interrupt, 'all')).toBe(true)

    const skillInjection = message({
      type: 'user',
      authorship_kind: 'skill_injection',
      blocks: [textBlock('Base directory for this skill: ...')],
    })
    expect(isVisibleInView(skillInjection, 'chat')).toBe(false)
    expect(isVisibleInView(skillInjection, 'chat-harness')).toBe(true)
    expect(isVisibleInView(skillInjection, 'all')).toBe(true)

    const toolResult = message({
      type: 'user',
      authorship_kind: 'tool_result',
      blocks: [kindBlock('tool_result')],
    })
    expect(isVisibleInView(toolResult, 'chat')).toBe(false)
    expect(isVisibleInView(toolResult, 'chat-harness')).toBe(false)
    expect(isVisibleInView(toolResult, 'all')).toBe(true)
  })

  it('null kind falls back to the legacy type rule in every view', () => {
    // A pre-reparse row (migrate→reparse deploy window, spec §4/§5): no authorship_kind yet, but
    // its TYPE qualifies and it carries real content — visible in every filtered view.
    const nullKindUser = message({ type: 'user', authorship_kind: null, blocks: [textBlock('hi')] })
    expect(isVisibleInView(nullKindUser, 'chat')).toBe(true)
    expect(isVisibleInView(nullKindUser, 'chat-harness')).toBe(true)
    expect(isVisibleInView(nullKindUser, 'all')).toBe(true)

    // Null kind but a type the legacy rule never admitted (system) — the fallback still excludes
    // it from the filtered views, exactly like an unclassified system row always has.
    const nullKindSystem = message({ type: 'system', authorship_kind: null, blocks: [textBlock('sys')] })
    expect(isVisibleInView(nullKindSystem, 'chat')).toBe(false)
    expect(isVisibleInView(nullKindSystem, 'chat-harness')).toBe(false)
    expect(isVisibleInView(nullKindSystem, 'all')).toBe(true)

    // Null kind, qualifying type, but no content to show (spec §4's trim rule) — still hidden in
    // both filtered views even though the type/kind gate passes.
    const nullKindEmpty = message({ type: 'assistant', authorship_kind: null, blocks: [kindBlock('thinking')] })
    expect(isVisibleInView(nullKindEmpty, 'chat')).toBe(false)
    expect(isVisibleInView(nullKindEmpty, 'chat-harness')).toBe(false)
    expect(isVisibleInView(nullKindEmpty, 'all')).toBe(true)
  })

  it('gates a chat-kind message on the same prose-visibility rule as legacy rows', () => {
    // 'claude' is in CHAT_KINDS, but a message whose only block is tool_use/tool_result/thinking/
    // empty-text still has nothing to show — the kind gate and the content gate are independent.
    const claudeToolOnly = message({
      type: 'assistant',
      authorship_kind: 'claude',
      blocks: [kindBlock('tool_use')],
    })
    expect(isVisibleInView(claudeToolOnly, 'chat')).toBe(false)
    expect(isVisibleInView(claudeToolOnly, 'all')).toBe(true)

    const claudeWithText = message({
      type: 'assistant',
      authorship_kind: 'claude',
      blocks: [textBlock('hello')],
    })
    expect(isVisibleInView(claudeWithText, 'chat')).toBe(true)
  })
})

beforeEach(() => {
  window.localStorage.clear()
})

describe('useViewMode', () => {
  it('defaults to chat and persists via introspect.view.v1', () => {
    const { result } = renderHook(() => useViewMode())
    expect(result.current.view).toBe('chat')

    act(() => result.current.setView('all'))
    expect(result.current.view).toBe('all')
    expect(window.localStorage.getItem(KEY)).toBe('all')
  })

  it('seeds from a pre-existing stored value — sticky across mounts', () => {
    window.localStorage.setItem(KEY, 'chat-harness')
    const { result } = renderHook(() => useViewMode())
    expect(result.current.view).toBe('chat-harness')
  })

  it('falls back to the default when the stored value is not a recognized ViewMode', () => {
    window.localStorage.setItem(KEY, 'bogus')
    const { result } = renderHook(() => useViewMode())
    expect(result.current.view).toBe('chat')
  })

  it('removes the legacy introspect.chatOnly.v1 key on first write', () => {
    window.localStorage.setItem(LEGACY_KEY, '1')
    const { result } = renderHook(() => useViewMode())
    // Zero-legacy: the old boolean key is never READ, even though it's still on disk.
    expect(result.current.view).toBe('chat')
    expect(window.localStorage.getItem(LEGACY_KEY)).toBe('1')

    act(() => result.current.setView('all'))
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull()
  })

  it('degrades to in-memory state when localStorage.setItem throws (private mode)', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    const { result } = renderHook(() => useViewMode())
    expect(() => act(() => result.current.setView('all'))).not.toThrow()
    // The write threw, but the in-memory state must still flip so the UI stays responsive.
    expect(result.current.view).toBe('all')
    spy.mockRestore()
  })

  it('reads the default without throwing when localStorage.getItem throws (private mode)', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })
    let rendered: ReturnType<typeof renderHook<ReturnType<typeof useViewMode>, unknown>> | undefined
    expect(() => {
      rendered = renderHook(() => useViewMode())
    }).not.toThrow()
    expect(rendered?.result.current.view).toBe('chat')
    spy.mockRestore()
  })
})
