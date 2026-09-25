import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlockOut, MessageOut } from '../src/api/types'
import {
  ALL_CATEGORIES_SET,
  CHAT_KINDS,
  PRESET_SETS,
  categoryOfBlock,
  isBlockCategorySelected,
  isVisibleInSelection,
  isVisibleInView,
  presetForSelection,
  readSelection,
  useCategorySelection,
  useViewMode,
  writeSelection,
  type CategorySlug,
} from '../src/lib/viewMode'

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

// PARITY PIN: mirrors server test_api_sessions.py's
// test_view_chat_and_harness_show_resolved_dispatch_rows_with_no_prose (final review C1) --
// change both together. Production shape: a dispatch tool_use row carries NO prose of its own
// (445 production rows, 0 with any text) -- `proseVisible` alone would hide the whole ROW,
// stranding the SubagentChip outside `all`. The third `isVisibleInView` argument is the
// resolved-dispatch tool_use id set (`useDispatchToolUseIds`, transcripts-context.ts in real
// callers); this suite exercises the pure predicate directly with plain Sets.
describe('isVisibleInView — resolved dispatch rows (final review C1)', () => {
  function toolUseBlock(id: string): BlockOut {
    return { block_index: 0, block_kind: 'tool_use', text_content: null, tool_name: 'Task', tool_use_id: id, is_error: null }
  }

  it('shows a prose-less dispatch row when its tool_use resolves to a captured subagent transcript', () => {
    const dispatchOnly = message({
      type: 'assistant',
      authorship_kind: 'claude',
      blocks: [toolUseBlock('tu-1')],
    })
    const resolved: ReadonlySet<string> = new Set(['tu-1'])
    expect(isVisibleInView(dispatchOnly, 'chat', resolved)).toBe(true)
    expect(isVisibleInView(dispatchOnly, 'chat-harness', resolved)).toBe(true)
    expect(isVisibleInView(dispatchOnly, 'all', resolved)).toBe(true)
  })

  it('keeps hiding a prose-less tool_use row whose id resolves to nothing', () => {
    const unresolved = message({
      type: 'assistant',
      authorship_kind: 'claude',
      blocks: [toolUseBlock('tu-2')],
    })
    expect(isVisibleInView(unresolved, 'chat', new Set(['tu-other']))).toBe(false)
    expect(isVisibleInView(unresolved, 'chat-harness', new Set(['tu-other']))).toBe(false)
    // Still visible in `all` -- the dispatch-resolution rule only widens the FILTERED views.
    expect(isVisibleInView(unresolved, 'all', new Set(['tu-other']))).toBe(true)
  })

  it('defaults to an empty dispatch set when the third argument is omitted', () => {
    // Bare unit-test render safety (a call site with no TranscriptsProvider in reach): omitting
    // the set must degrade to "no dispatch resolves", never throw.
    const dispatchOnly = message({
      type: 'assistant',
      authorship_kind: 'claude',
      blocks: [toolUseBlock('tu-3')],
    })
    expect(isVisibleInView(dispatchOnly, 'chat')).toBe(false)
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

// --- category filter (Task T10) -------------------------------------------------------------

function toolUseBlock(id: string, over: Partial<BlockOut> = {}): BlockOut {
  return {
    block_index: 0,
    block_kind: 'tool_use',
    text_content: null,
    tool_name: 'Bash',
    tool_use_id: id,
    is_error: null,
    ...over,
  }
}

describe('PRESET_SETS / presetForSelection', () => {
  // Corrected 2026-09-22 against the server's T9 equivalence suite: `select=` prunes blocks
  // within an already-visible row (`view=` never did), so reproducing `view=chat`'s exact rows
  // AND blocks needs `claude-thinking` in the `chat`/`chat-harness` sets too — see viewMode.ts's
  // PRESET_SETS doc.
  it('chat = you-chat + claude-chat + claude-thinking, chat-harness adds harness-system, all is every slug', () => {
    expect(PRESET_SETS.chat).toEqual(new Set(['you-chat', 'claude-chat', 'claude-thinking']))
    expect(PRESET_SETS['chat-harness']).toEqual(
      new Set(['you-chat', 'claude-chat', 'claude-thinking', 'harness-system']),
    )
    expect(PRESET_SETS.all).toEqual(ALL_CATEGORIES_SET)
  })

  it('recognizes each preset back from its exact set', () => {
    expect(presetForSelection(PRESET_SETS.chat)).toBe('chat')
    expect(presetForSelection(PRESET_SETS['chat-harness'])).toBe('chat-harness')
    expect(presetForSelection(PRESET_SETS.all)).toBe('all')
  })

  it('returns null for a custom combination', () => {
    expect(presetForSelection(new Set(['you-chat', 'tool-traffic']))).toBeNull()
  })
})

// A byte-for-byte port of the server's `_categorize` (sessions.py, Task T9) — every case here
// mirrors one of that function's own test cases (test_api_sessions.py) so the two can never
// drift silently.
describe('categoryOfBlock', () => {
  it('every block of a tool_result-AUTHORSHIP message is tool-traffic, regardless of block kind', () => {
    const msg = message({ authorship_kind: 'tool_result' })
    for (const block of [textBlock('x'), kindBlock('thinking'), kindBlock('image'), toolUseBlock('tu-1')]) {
      expect(categoryOfBlock(block, msg)).toBe('tool-traffic')
    }
  })

  it('an UNRESOLVED tool_use BLOCK is tool-traffic regardless of its message authorship (dispatchToolUseIds omitted or non-matching)', () => {
    for (const kind of ['human_typed', 'harness_meta', 'claude', 'dispatch', 'coordinator']) {
      // No third argument at all -- degrades to an empty resolved set.
      expect(categoryOfBlock(toolUseBlock('tu-1'), message({ authorship_kind: kind }))).toBe(
        'tool-traffic',
      )
      // A resolved set given, but this block's id isn't in it.
      expect(
        categoryOfBlock(toolUseBlock('tu-1'), message({ authorship_kind: kind }), new Set(['tu-other'])),
      ).toBe('tool-traffic')
    }
  })

  // Owner ruling 2026-09-23 (Task T12): a RESOLVED-dispatch tool_use block -- its id present in
  // the caller-supplied `dispatchToolUseIds` set (the same set `useDispatchToolUseIds`/
  // `SubagentChip` use to decide a chip resolves) -- is `claude-chat`, a doorway into a
  // Claude-voiced conversation, not mechanical traffic. This wins over the family branches below
  // regardless of the message's own authorship kind, same as the unresolved tool_use rule above.
  it('a RESOLVED tool_use BLOCK (its id in dispatchToolUseIds) is claude-chat', () => {
    const resolved = new Set(['tu-1'])
    for (const kind of ['claude', 'dispatch', 'coordinator', 'human_typed', 'harness_meta']) {
      expect(
        categoryOfBlock(toolUseBlock('tu-1'), message({ authorship_kind: kind }), resolved),
      ).toBe('claude-chat')
    }
    // A tool_result-AUTHORSHIP message still wins over even a resolved id (priority 1 first).
    expect(
      categoryOfBlock(toolUseBlock('tu-1'), message({ authorship_kind: 'tool_result' }), resolved),
    ).toBe('tool-traffic')
  })

  // NOTE: unlike `tool_use`, there is no dedicated `block_kind === 'tool_result'` branch in the
  // server's `_categorize` (or here) -- a tool_result BLOCK is only ever tool-traffic via the
  // message-level `authorship_kind === 'tool_result'` override above (branch 1), because the
  // classifier ALWAYS assigns that message kind whenever a record carries a tool_result block
  // (schema/authorship.py `_classify_user`, rule 2: "tool result (block authoritative)"). A lone
  // tool_result block on a differently-classified message is structurally unreachable in
  // production, so (matching the server's own test suite, which doesn't test it either) it falls
  // through to the ordinary family rules like any other non-thinking block would.

  it('the you-chat family: human_typed/queued/inferred, attachment_queued_human, interrupt_marker', () => {
    for (const kind of [
      'human_typed',
      'human_queued',
      'human_inferred',
      'attachment_queued_human',
      'interrupt_marker',
    ]) {
      expect(categoryOfBlock(textBlock('hi'), message({ authorship_kind: kind }))).toBe(
        'you-chat',
      )
    }
  })

  it('the claude family splits thinking from everything else', () => {
    for (const kind of ['claude', 'dispatch', 'coordinator']) {
      expect(categoryOfBlock(kindBlock('thinking'), message({ authorship_kind: kind }))).toBe(
        'claude-thinking',
      )
      for (const blockKind of ['text', 'image', 'document', 'fallback']) {
        expect(categoryOfBlock(kindBlock(blockKind), message({ authorship_kind: kind }))).toBe(
          'claude-chat',
        )
      }
    }
  })

  it('floors everything else (system, skill_injection, unclassified, NULL, unknown-future kinds) to harness-system', () => {
    for (const kind of ['system', 'skill_injection', 'unclassified', 'harness_meta', 'some_future_kind', null]) {
      expect(categoryOfBlock(textBlock('x'), message({ authorship_kind: kind }))).toBe(
        'harness-system',
      )
    }
    // A `thinking` block outside the claude family -- structurally unreachable in production
    // (only an assistant record ever emits thinking, and that always classifies "claude"), but
    // the server's own test proves it explicitly as a forward-tolerance case.
    expect(categoryOfBlock(kindBlock('thinking'), message({ authorship_kind: 'harness_meta' }))).toBe(
      'harness-system',
    )
  })

  it('an unknown block kind is still categorized by message voice, never dropped', () => {
    const unknown = kindBlock('mystery')
    expect(categoryOfBlock(unknown, message({ authorship_kind: 'human_typed' }))).toBe('you-chat')
    expect(categoryOfBlock(unknown, message({ authorship_kind: 'system' }))).toBe(
      'harness-system',
    )
  })

  // No legacy type-based tolerance for a NULL (pre-backfill) authorship_kind here, unlike
  // `isVisibleInView`'s `legacyFallback` -- the server's `_categorize` floors NULL to
  // harness-system unconditionally, never consulting `message.type`. Confirmed against the
  // server's own exhaustiveness test (`test_categorize_harness_system_is_the_exhaustive_floor`,
  // which includes `None` in its harness_kinds list).
  it('a NULL authorship_kind floors to harness-system regardless of message.type', () => {
    for (const type of ['user', 'assistant', 'attachment', 'system']) {
      expect(categoryOfBlock(textBlock('x'), message({ type, authorship_kind: null }))).toBe(
        'harness-system',
      )
    }
  })
})

describe('isVisibleInSelection', () => {
  it('a row is visible when at least one block is selected', () => {
    const msg = message({ authorship_kind: 'human_typed', blocks: [textBlock('hi')] })
    expect(isVisibleInSelection(msg, new Set(['you-chat']))).toBe(true)
    expect(isVisibleInSelection(msg, new Set(['claude-chat']))).toBe(false)
  })

  it('a row disappears when none of its blocks are selected', () => {
    const msg = message({
      authorship_kind: 'claude',
      blocks: [
        { block_index: 0, block_kind: 'thinking', text_content: 'mulling', tool_name: null, tool_use_id: null, is_error: null },
      ],
    })
    expect(isVisibleInSelection(msg, new Set(['claude-chat']))).toBe(false)
    expect(isVisibleInSelection(msg, new Set(['claude-thinking']))).toBe(true)
  })

  // Server-confirmed nuance (test_api_sessions.py's discovered-bug writeup, nuance 4): an EMPTY
  // text block never counts toward ROW VISIBILITY, even though `categoryOfBlock` still slots it
  // into its message's family -- mirrors `_block_matches_categories`' extra guard, itself
  // mirroring `isVisibleInView`'s `_prose_visible()`. Without this, a resolved-dispatch-shaped
  // row (tool_use + an empty companion text block, the CLI's real production shape) would read
  // as "visible" under a selection that excludes tool-traffic purely because its EMPTY text
  // block's category (claude-chat) happens to be selected -- an empty ghost row with nothing
  // actually rendered in it (Block()'s own text case already refuses to render empty text, and
  // the tool_use block is separately gated out), instead of correctly disappearing entirely.
  it('an empty text block never counts toward row visibility on its own, even if its category is selected', () => {
    const emptyTextOnly = message({
      authorship_kind: 'claude',
      blocks: [textBlock('')],
    })
    expect(isVisibleInSelection(emptyTextOnly, new Set(['claude-chat']))).toBe(false)

    const nullTextOnly = message({
      authorship_kind: 'claude',
      blocks: [textBlock(null)],
    })
    expect(isVisibleInSelection(nullTextOnly, new Set(['claude-chat']))).toBe(false)

    // The real production shape: a resolved-dispatch tool_use plus an empty companion text
    // block -- invisible under a selection that omits tool-traffic, since NEITHER block counts.
    const dispatchShaped = message({
      authorship_kind: 'dispatch',
      blocks: [toolUseBlock('tu-1'), textBlock('')],
    })
    expect(isVisibleInSelection(dispatchShaped, PRESET_SETS.chat)).toBe(false)

    // A NON-empty text block still counts as always.
    const withRealText = message({
      authorship_kind: 'claude',
      blocks: [textBlock('real prose')],
    })
    expect(isVisibleInSelection(withRealText, new Set(['claude-chat']))).toBe(true)
  })

  // Server contract update (T17 follow-up, 2026-09-25): a message with ZERO content blocks
  // categorizes as `harness-system` at the MESSAGE level (there's no block to categorize, so this
  // is a message-level rule, not a `categoryOfBlock` case) -- visible iff `harness-system` is in
  // the selection. This restores "select=<all five> ≡ old view=all shows everything", including
  // blockless furniture (bare `system` records, non-rescued zero-block `attachment` stubs), and
  // means "show all message types" (ALL_CATEGORIES_SET) is now truly complete again.
  it('a zero-block message is hidden under the chat set, visible under any set containing harness-system', () => {
    const msg = message({ type: 'attachment', authorship_kind: null, blocks: [] })
    expect(isVisibleInSelection(msg, PRESET_SETS.chat)).toBe(false)
    expect(isVisibleInSelection(msg, new Set(['you-chat', 'claude-chat']))).toBe(false)
    expect(isVisibleInSelection(msg, PRESET_SETS['chat-harness'])).toBe(true)
    expect(isVisibleInSelection(msg, new Set(['harness-system']))).toBe(true)
    expect(isVisibleInSelection(msg, ALL_CATEGORIES_SET)).toBe(true)
  })

  // Owner ruling 2026-09-23 (Task T12; server test flipped to
  // test_select_reproduces_resolved_dispatch_chip_visibility_as_claude_chat): a RESOLVED
  // dispatch row with no other content is now VISIBLE under the `chat` preset -- its tool_use
  // block is claude-chat, which `chat` includes -- and the ruling's flip side: it's invisible
  // once claude-chat is unselected and only tool-traffic is selected (its block no longer
  // qualifies there), while an ORDINARY unresolved tool_use row is unaffected either way.
  it('a resolved dispatch row with no other content is visible under chat, invisible under tool-traffic alone', () => {
    const msg = message({ authorship_kind: 'dispatch', blocks: [toolUseBlock('tu-1')] })
    const dispatchIds = new Set(['tu-1'])
    expect(isVisibleInSelection(msg, PRESET_SETS.chat, dispatchIds)).toBe(true)
    expect(isVisibleInSelection(msg, new Set(['tool-traffic']), dispatchIds)).toBe(false)
    // Without the resolved-id context (bare unit render outside a TranscriptsProvider), it
    // degrades to "unresolved" -- invisible under chat, visible once tool-traffic is selected --
    // exactly the PRE-ruling behavior, so a caller that can't reach the context never breaks.
    expect(isVisibleInSelection(msg, PRESET_SETS.chat)).toBe(false)
    expect(isVisibleInSelection(msg, new Set(['tool-traffic']))).toBe(true)
  })

  it('an unresolved tool_use row is unaffected by the ruling: invisible under chat, visible under tool-traffic', () => {
    const msg = message({ authorship_kind: 'dispatch', blocks: [toolUseBlock('tu-2')] })
    const dispatchIds = new Set(['tu-other'])
    expect(isVisibleInSelection(msg, PRESET_SETS.chat, dispatchIds)).toBe(false)
    expect(isVisibleInSelection(msg, new Set(['tool-traffic']), dispatchIds)).toBe(true)
  })
})

describe('isBlockCategorySelected', () => {
  it('gates a single block on whether its category is in the selection', () => {
    const msg = message({ authorship_kind: 'claude' })
    expect(isBlockCategorySelected(textBlock('hi'), msg, new Set(['claude-chat']))).toBe(true)
    expect(isBlockCategorySelected(textBlock('hi'), msg, new Set(['you-chat']))).toBe(false)
  })

  it('threads dispatchToolUseIds through to categoryOfBlock for a resolved tool_use block', () => {
    const msg = message({ authorship_kind: 'dispatch' })
    const resolved = new Set(['tu-1'])
    expect(
      isBlockCategorySelected(toolUseBlock('tu-1'), msg, new Set(['claude-chat']), resolved),
    ).toBe(true)
    expect(
      isBlockCategorySelected(toolUseBlock('tu-1'), msg, new Set(['tool-traffic']), resolved),
    ).toBe(false)
    // Omitted -> unresolved -> tool-traffic.
    expect(isBlockCategorySelected(toolUseBlock('tu-1'), msg, new Set(['tool-traffic']))).toBe(
      true,
    )
  })
})

// Task T17 (owner ruling 2026-09-25): `?view=` is deleted entirely -- `select=` is the only wire
// param, absent defaults to the chat preset, and a legacy `?view=` in an arriving URL is simply
// ignored (opens at default) rather than consulted. Server contract (parallel task, frozen): same
// rule -- `view=` is deleted server-side too.
describe('readSelection', () => {
  it('defaults to the chat preset when ?select= is absent', () => {
    expect(readSelection(new URLSearchParams())).toEqual(PRESET_SETS.chat)
  })

  it('ignores a legacy ?view= entirely -- opens at the chat default, not the named preset', () => {
    expect(readSelection(new URLSearchParams('view=chat-harness'))).toEqual(PRESET_SETS.chat)
    expect(readSelection(new URLSearchParams('view=all'))).toEqual(PRESET_SETS.chat)
    expect(readSelection(new URLSearchParams('view=bogus'))).toEqual(PRESET_SETS.chat)
  })

  it('reads a custom combination from ?select=', () => {
    const params = new URLSearchParams('select=you-chat,claude-thinking')
    expect(readSelection(params)).toEqual(new Set(['you-chat', 'claude-thinking']))
  })

  it('select= wins over an accompanying legacy ?view=', () => {
    const params = new URLSearchParams('view=all&select=you-chat')
    expect(readSelection(params)).toEqual(new Set(['you-chat']))
  })

  it('drops unrecognized slugs out of ?select= but keeps the recognized ones', () => {
    const params = new URLSearchParams('select=you-chat,not-a-real-slug')
    expect(readSelection(params)).toEqual(new Set(['you-chat']))
  })

  it('falls back to the chat default when ?select= parses to nothing usable', () => {
    expect(readSelection(new URLSearchParams('select='))).toEqual(PRESET_SETS.chat)
    expect(readSelection(new URLSearchParams('select=not-a-real-slug'))).toEqual(PRESET_SETS.chat)
  })
})

describe('writeSelection', () => {
  it('writes no param at all when the selection equals the chat default (clean URLs)', () => {
    const prev = new URLSearchParams('select=you-chat')
    const next = writeSelection(prev, PRESET_SETS.chat)
    expect(next.has('select')).toBe(false)
    expect(next.has('view')).toBe(false)
  })

  it('writes ?select= for chat-harness and all too -- only the chat default is ever omitted', () => {
    const harness = writeSelection(new URLSearchParams(), PRESET_SETS['chat-harness'])
    expect(new Set(harness.get('select')?.split(','))).toEqual(PRESET_SETS['chat-harness'])
    expect(harness.has('view')).toBe(false)

    const all = writeSelection(new URLSearchParams(), PRESET_SETS.all)
    expect(new Set(all.get('select')?.split(','))).toEqual(ALL_CATEGORIES_SET)
    expect(all.has('view')).toBe(false)
  })

  it('writes ?select= for a custom combination', () => {
    const prev = new URLSearchParams()
    const next = writeSelection(prev, new Set<CategorySlug>(['you-chat', 'tool-traffic']))
    expect(new Set(next.get('select')?.split(','))).toEqual(new Set(['you-chat', 'tool-traffic']))
    expect(next.has('view')).toBe(false)
  })

  it('never writes ?view= -- that param is dead code on the write side', () => {
    const next = writeSelection(new URLSearchParams(), new Set<CategorySlug>(['you-chat']))
    expect(next.has('view')).toBe(false)
  })

  it('preserves unrelated params and does not mutate the input', () => {
    const prev = new URLSearchParams('q=other')
    const next = writeSelection(prev, PRESET_SETS.all)
    expect(next.get('q')).toBe('other')
    expect(prev.has('select')).toBe(false)
  })
})

describe('useCategorySelection', () => {
  function wrapper(initialPath: string) {
    return ({ children }: { children: ReactNode }) =>
      createElement(MemoryRouter, { initialEntries: [initialPath] }, children)
  }

  it('defaults to the chat preset with no URL params', () => {
    const { result } = renderHook(() => useCategorySelection(), { wrapper: wrapper('/s/x') })
    expect(result.current.selection).toEqual(PRESET_SETS.chat)
  })

  it('seeds from ?select= already in the URL', () => {
    const { result } = renderHook(() => useCategorySelection(), {
      wrapper: wrapper(`/s/x?select=${[...ALL_CATEGORIES_SET].join(',')}`),
    })
    expect(result.current.selection).toEqual(ALL_CATEGORIES_SET)
  })

  it('ignores a legacy ?view= in the URL -- seeds the chat default', () => {
    const { result } = renderHook(() => useCategorySelection(), {
      wrapper: wrapper('/s/x?view=all'),
    })
    expect(result.current.selection).toEqual(PRESET_SETS.chat)
  })

  it('setSelection updates the read-back selection', () => {
    const { result } = renderHook(() => useCategorySelection(), { wrapper: wrapper('/s/x') })
    act(() => result.current.setSelection(new Set(['you-chat'])))
    expect(result.current.selection).toEqual(new Set(['you-chat']))
  })
})
