import { act, fireEvent, render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlockOut, MessageOut, TranscriptInfo } from '../src/api/types'
import { MessageTurn, speakerFor } from '../src/components/reader/MessageTurn'
import { TranscriptsProvider } from '../src/components/reader/transcripts-context'

// MessageTurn is deliberately tested UN-virtualized (plain render, no Virtuoso) — jsdom has no
// layout engine, so these assertions stay honest: class names, DOM order, and markdown output
// are all real; nothing depends on measured heights.

// Route context is now a real dependency of the component (useEntryHref reads useParams), so
// every render needs a Router with a matched Route — bare MemoryRouter no longer suffices once a
// test cares about the resulting href.
const SESSION_UUID = 'sess-1234'
function renderTurn(ui: ReactElement, path = `/s/${SESSION_UUID}`, pattern = '/s/:uuid') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pattern} element={ui} />
      </Routes>
    </MemoryRouter>,
  )
}

function textBlock(index: number, text: string): BlockOut {
  return {
    block_index: index,
    block_kind: 'text',
    text_content: text,
    tool_name: null,
    tool_use_id: null,
    is_error: null,
  }
}

function message(over: Partial<MessageOut> = {}): MessageOut {
  return {
    record_uuid: 'rec-1',
    parent_uuid: null,
    type: 'user',
    model: null,
    timestamp: '2026-07-19T14:03:00Z',
    blocks: [textBlock(0, 'hello')],
    authorship_kind: null,
    authorship_basis: null,
    authorship_detail: null,
    ...over,
  }
}

function turnOf(container: HTMLElement): HTMLElement {
  const turn = container.querySelector<HTMLElement>('.message-turn')
  expect(turn).not.toBeNull()
  return turn as HTMLElement
}

describe('voice accents', () => {
  it('marks user turns with the dawn accent class', () => {
    const { container } = renderTurn(<MessageTurn message={message({ type: 'user' })} />)
    expect(turnOf(container).classList.contains('turn-user')).toBe(true)
  })

  it('marks assistant turns with the dragonfly accent class', () => {
    const { container } = renderTurn(<MessageTurn message={message({ type: 'assistant' })} />)
    expect(turnOf(container).classList.contains('turn-assistant')).toBe(true)
  })

  it('marks system turns with the mist accent class', () => {
    const { container } = renderTurn(<MessageTurn message={message({ type: 'system' })} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
  })

  it('treats unknown record types as system-accented', () => {
    const { container } = renderTurn(<MessageTurn message={message({ type: 'summary' })} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
  })
})

// §5/§6 rework: the `{}` inspect button is retired. The speaker NAME is now the raw-record
// trigger (same onInspect wiring), and HH:MM is a copy-on-click deeplink anchor. Six contract
// cases from the task brief, each pinned as its own test.
describe('eyebrow', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
  })

  afterEach(() => {
    vi.useRealTimers()
    // @ts-expect-error - deleting a stubbed test-only global
    delete navigator.clipboard
  })

  it('renders "SPEAKER · HH:MM" with the local time', () => {
    const iso = '2026-07-19T14:03:00Z'
    const { container } = renderTurn(
      <MessageTurn message={message({ type: 'user', timestamp: iso })} />,
    )
    const local = new Date(iso)
    const hhmm = `${String(local.getHours()).padStart(2, '0')}:${String(
      local.getMinutes(),
    ).padStart(2, '0')}`
    const eyebrow = container.querySelector('.turn-eyebrow')
    expect(eyebrow?.textContent).toBe(`YOU · ${hhmm}`)
  })

  it('labels assistant turns CLAUDE and system turns SYSTEM', () => {
    const a = renderTurn(<MessageTurn message={message({ type: 'assistant' })} />)
    expect(a.container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^CLAUDE · /)
    const s = renderTurn(<MessageTurn message={message({ type: 'system' })} />)
    expect(s.container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM · /)
  })

  // Case 1: the `{}` affordance no longer exists anywhere, even when onInspect is wired.
  it('never renders the retired {} inspect button, even with onInspect supplied', () => {
    renderTurn(<MessageTurn message={message()} onInspect={vi.fn()} />)
    expect(screen.queryByText('{}')).toBeNull()
  })

  // Case 2: the speaker name is the raw-record trigger when onInspect is wired; plain text
  // (no button role) in the un-wired unit-test case — same conditional the `{}` had.
  it('makes the speaker name a button that calls onInspect with the record uuid', () => {
    const onInspect = vi.fn()
    renderTurn(
      <MessageTurn message={message({ type: 'assistant', record_uuid: 'rec-42' })} onInspect={onInspect} />,
    )
    const btn = screen.getByRole('button', { name: /view raw record — CLAUDE/ })
    fireEvent.click(btn)
    expect(onInspect).toHaveBeenCalledWith('rec-42')
  })

  it('renders the speaker name as plain text (no button role) when onInspect is absent', () => {
    const { container } = renderTurn(<MessageTurn message={message({ type: 'assistant' })} />)
    expect(screen.queryByRole('button', { name: /view raw record/ })).toBeNull()
    // Bare text node (no wrapping element), same as the pre-rework un-wired case — assert via
    // the eyebrow's textContent rather than getByText, which can't isolate text diluted by the
    // adjacent " · HH:MM" sibling content.
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^CLAUDE · /)
  })

  // Case 3: the time is a deeplink anchor scoped to the current route — session-level under
  // /s/:uuid, nested under the agent segment on the subagent route.
  it('renders the time as a.turn-time deeplinking to this record under the session route', () => {
    const { container } = renderTurn(<MessageTurn message={message({ record_uuid: 'rec-7' })} />)
    const anchor = container.querySelector('a.turn-time')
    expect(anchor).not.toBeNull()
    expect(anchor?.getAttribute('href')).toBe(`/s/${SESSION_UUID}/m/rec-7`)
  })

  it('nests the deeplink under the agent segment on the subagent route', () => {
    const { container } = renderTurn(
      <MessageTurn message={message({ record_uuid: 'rec-7' })} />,
      '/s/sess-1234/a/beef42',
      '/s/:uuid/a/:agentHex',
    )
    const anchor = container.querySelector('a.turn-time')
    expect(anchor?.getAttribute('href')).toBe('/s/sess-1234/a/beef42/m/rec-7')
  })

  // Case 4: a plain primary click copies the absolute deeplink, prevents the native navigation,
  // and flashes a "copied" whisper that clears itself after 1600ms.
  it('plain left-click copies the deeplink, prevents default, and flashes "copied"', () => {
    const { container } = renderTurn(<MessageTurn message={message({ record_uuid: 'rec-7' })} />)
    const anchor = container.querySelector<HTMLAnchorElement>('a.turn-time')
    expect(anchor).not.toBeNull()
    const href = anchor!.getAttribute('href')!

    // fireEvent.click's return value is the DOM dispatchEvent result: false means some handler
    // called preventDefault() on the (cancelable) click event — i.e. no native navigation.
    const notPrevented = fireEvent.click(anchor!)
    expect(notPrevented).toBe(false)

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(window.location.origin + href)
    expect(container.querySelector('.turn-copied')?.textContent).toBe('copied')

    act(() => {
      vi.advanceTimersByTime(1600)
    })
    expect(container.querySelector('.turn-copied')).toBeNull()
  })

  // Case 5: modified clicks stay native — only a plain primary click copies.
  it('does not copy on ctrl-click or meta-click', () => {
    const { container } = renderTurn(<MessageTurn message={message({ record_uuid: 'rec-7' })} />)
    const anchor = container.querySelector<HTMLAnchorElement>('a.turn-time')!

    fireEvent.click(anchor, { ctrlKey: true })
    fireEvent.click(anchor, { metaKey: true })

    expect(navigator.clipboard.writeText).not.toHaveBeenCalled()
  })

  // Case 6: absent timestamp — no anchor, no ` · ` separator, speaker alone (re-pinned).
  it('omits the time (no anchor, no separator) when the timestamp is absent', () => {
    const { container } = renderTurn(<MessageTurn message={message({ timestamp: null })} />)
    expect(container.querySelector('a.turn-time')).toBeNull()
    expect(container.querySelector('.turn-eyebrow')?.textContent).toBe('YOU')
  })
})

describe('block ordering and dispatch', () => {
  it('renders blocks in block_index order even when the array is shuffled', () => {
    const shuffled = message({
      blocks: [textBlock(2, 'third'), textBlock(0, 'first'), textBlock(1, 'second')],
    })
    const { container } = renderTurn(<MessageTurn message={shuffled} />)
    const paragraphs = [...container.querySelectorAll('.markdown-prose p')].map(
      (p) => p.textContent,
    )
    expect(paragraphs).toEqual(['first', 'second', 'third'])
  })

  it('dispatches non-text blocks to their Task-6 renderers (tool row + thinking glyph)', () => {
    const toolMsg = message({
      blocks: [
        {
          block_index: 0,
          block_kind: 'tool_use',
          text_content: null,
          tool_name: 'Bash',
          tool_use_id: 'tu-1',
          is_error: null,
        },
        {
          block_index: 1,
          block_kind: 'thinking',
          text_content: 'private',
          tool_name: null,
          tool_use_id: null,
          is_error: null,
        },
      ],
    })
    // No TranscriptsProvider here, so the unmatched tool_use degrades to a plain ToolBlock.
    // The router wrapper is still required, though: SubagentChip (Task 9) reads the current
    // ?projects= via useSearchParams() unconditionally, before the no-match check runs.
    const { container } = renderTurn(<MessageTurn message={toolMsg} />)
    expect(container.querySelector('.tool-block')).not.toBeNull()
    expect(container.querySelector('.block-stub')).toBeNull()
    expect(container.querySelector('.thinking-glyph')).not.toBeNull()
  })
})

describe('conversation-only block hiding (view prop)', () => {
  function toolBlock(index: number, over: Partial<BlockOut> = {}): BlockOut {
    return {
      block_index: index,
      block_kind: 'tool_use',
      text_content: null,
      tool_name: 'Task',
      tool_use_id: 'tu-1',
      is_error: null,
      ...over,
    }
  }

  it.each(['chat', 'chat-harness'] as const)(
    'hides tool_use and tool_result blocks but keeps text/thinking/image (view=%s)',
    (view) => {
      const msg = message({
        blocks: [
          textBlock(0, 'kept text'),
          toolBlock(1, { block_kind: 'tool_use', tool_name: 'Bash', tool_use_id: 'no-match' }),
          toolBlock(2, { block_kind: 'tool_result', text_content: 'tool output' }),
          {
            block_index: 3,
            block_kind: 'thinking',
            text_content: 'private',
            tool_name: null,
            tool_use_id: null,
            is_error: null,
          },
          {
            block_index: 4,
            block_kind: 'image',
            text_content: null,
            tool_name: null,
            tool_use_id: null,
            is_error: null,
          },
        ],
      })
      const { container } = renderTurn(<MessageTurn message={msg} view={view} />)
      // tool_use (as ToolBlock, no transcript match) and tool_result both vanish.
      expect(container.querySelector('.tool-block')).toBeNull()
      // conversational blocks remain.
      expect(container.textContent).toContain('kept text')
      expect(container.querySelector('.thinking-glyph')).not.toBeNull()
      expect(container.textContent).toContain('[image]')
    },
  )

  // Spec §6/§10.7c supersedes the earlier "chip disappears with its tool_use" ledger #7 read: a
  // RESOLVED subagent dispatch survives in every view — SubagentChip is the reader's sole doorway
  // into a subagent transcript, so filtering it out with ordinary tool noise would delete subagent
  // navigation from the default view. Only an UNRESOLVED tool_use (no matching transcript) stays
  // hidden outside `all` (next test). A leading text block keeps the ROW itself past
  // isVisibleInView's prose-visibility gate (lib/viewMode, untouched by this task) — a tool_use
  // block alone never counts as "content" there, so a tool_use-only row would vanish at the ROW
  // level regardless of the block-level behavior this test is isolating.
  it.each(['chat', 'chat-harness'] as const)(
    'keeps the subagent chip when its tool_use resolves to a dispatch (view=%s)',
    (view) => {
      const dispatch: TranscriptInfo = {
        id: 2,
        kind: 'subagent',
        agent_hex_id: 'a1b2c3',
        agent_type: 'Explore',
        agent_description: null,
        parent_tool_use_id: 'tu-1',
      }
      const msg = message({ blocks: [textBlock(0, 'dispatching'), toolBlock(1)] })
      renderTurn(
        <TranscriptsProvider value={{ sessionUuid: 'sess', transcripts: [dispatch] }}>
          <MessageTurn message={msg} view={view} />
        </TranscriptsProvider>,
      )
      expect(screen.getByRole('link', { name: /view transcript/ })).not.toBeNull()
      expect(screen.getByText(/subagent/)).not.toBeNull()
    },
  )

  it('does not fall back to a plain ToolBlock for an unresolved tool_use in a filtered view, even with a TranscriptsProvider present', () => {
    const unrelated: TranscriptInfo = {
      id: 3,
      kind: 'subagent',
      agent_hex_id: 'zzz',
      agent_type: 'Explore',
      agent_description: null,
      parent_tool_use_id: 'different-tool-use-id',
    }
    const msg = message({ blocks: [textBlock(0, 'thinking about it'), toolBlock(1)] })
    const { container } = renderTurn(
      <TranscriptsProvider value={{ sessionUuid: 'sess', transcripts: [unrelated] }}>
        <MessageTurn message={msg} view="chat-harness" />
      </TranscriptsProvider>,
    )
    expect(container.querySelector('.tool-block')).toBeNull()
    expect(screen.queryByText(/subagent/)).toBeNull()
  })

  it('renders tool blocks normally when view is all (default, no prop)', () => {
    const msg = message({ blocks: [toolBlock(0, { block_kind: 'tool_result', text_content: 'x' })] })
    const { container } = renderTurn(<MessageTurn message={msg} />)
    expect(container.querySelector('.tool-block')).not.toBeNull()
  })
})

// Task P4-F1: a block-bearing attachment is a rescued human queued-command — labelled
// SYSTEM (YOU), dawn (user) accent — while a zero-block attachment is harness furniture that
// keeps the plain SYSTEM treatment in full mode and vanishes entirely under a filtered view.
describe('attachment voice (rescued queued commands)', () => {
  function accentOf(container: HTMLElement): string {
    const inner = container.querySelector<HTMLElement>('.message-turn > div')
    expect(inner).not.toBeNull()
    return (inner as HTMLElement).style.borderLeft
  }

  it('labels a block-bearing attachment SYSTEM (YOU) with the dawn accent', () => {
    const msg = message({ type: 'attachment', blocks: [textBlock(0, 'queued human words')] })
    const { container } = renderTurn(<MessageTurn message={msg} />)
    expect(turnOf(container).classList.contains('turn-attachment')).toBe(true)
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM \(YOU\) · /)
    expect(accentOf(container)).toContain('var(--dawn)')
    expect(container.textContent).toContain('queued human words')
  })

  it('keeps a zero-block attachment as plain SYSTEM in full mode (mist accent)', () => {
    const msg = message({ type: 'attachment', blocks: [] })
    const { container } = renderTurn(<MessageTurn message={msg} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM · /)
    expect(accentOf(container)).toContain('var(--mist)')
  })

  it('hides a zero-block attachment entirely under a filtered view', () => {
    const msg = message({ type: 'attachment', blocks: [] })
    const { container } = renderTurn(<MessageTurn message={msg} view="chat" />)
    expect(container.querySelector('.message-turn')).toBeNull()
    expect(container.textContent).toBe('')
  })

  it('keeps a block-bearing attachment visible under a filtered view', () => {
    const msg = message({ type: 'attachment', blocks: [textBlock(0, 'still a human turn')] })
    const { container } = renderTurn(<MessageTurn message={msg} view="chat" />)
    expect(container.querySelector('.message-turn')).not.toBeNull()
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM \(YOU\) · /)
    expect(container.textContent).toContain('still a human turn')
  })
})

describe('markdown prose', () => {
  it('renders bold and inline code', () => {
    const { container } = renderTurn(
      <MessageTurn message={message({ blocks: [textBlock(0, '**bold** and `inline`')] })} />,
    )
    expect(container.querySelector('.markdown-prose strong')?.textContent).toBe('bold')
    expect(container.querySelector('.markdown-prose code')?.textContent).toBe('inline')
  })

  it('never mounts raw HTML — a <script> in markdown does not reach the DOM', () => {
    const hostile = message({
      blocks: [textBlock(0, 'before\n\n<script>window.pwned = true</script>\n\nafter')],
    })
    const { container } = renderTurn(<MessageTurn message={hostile} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('before')
    expect(container.textContent).toContain('after')
  })

  it('renders an unknown fence language (```notalang) as a plain code block without throwing', () => {
    const fenced = message({
      blocks: [textBlock(0, '```notalang\nweird ~~ stuff <<>>\n```')],
    })
    const { container } = renderTurn(<MessageTurn message={fenced} />)
    const code = container.querySelector('.markdown-prose pre code')
    expect(code).not.toBeNull()
    expect(code?.textContent).toContain('weird ~~ stuff <<>>')
  })

  it('highlights a known fence language with hljs token spans', () => {
    const fenced = message({
      blocks: [textBlock(0, '```js\nconst x = "still water"\n```')],
    })
    const { container } = renderTurn(<MessageTurn message={fenced} />)
    expect(container.querySelector('.markdown-prose pre code .hljs-keyword')).not.toBeNull()
  })
})

// Task 6 (authorship spec §3.3): speakerFor(message) replaces the old voiceOf/SPEAKER/ACCENT
// trio. Every row of the §3.3 kind→label table gets its own case here, keyed off authorship_kind
// + authorship_detail alone — speakerFor is tested directly (not through the DOM) so a label typo
// fails at the exact row it belongs to.
const cases: Array<[string, string | null, string]> = [
  ['human_typed', null, 'YOU'],
  ['human_queued', null, 'YOU'],
  ['human_inferred', null, 'YOU'],
  ['claude', null, 'CLAUDE'],
  ['dispatch', null, 'CLAUDE (DISPATCH)'],
  ['coordinator', null, 'CLAUDE (COORDINATOR)'],
  ['tool_result', 'Bash', 'SYSTEM (TOOL RESULT)'],
  ['skill_injection', 'superpowers:brainstorming', 'SYSTEM (SKILL: brainstorming)'], // prefix stripped
  ['tool_injection', 'ToolSearch', 'SYSTEM (INJECTED: toolsearch)'],
  ['tool_injection', null, 'SYSTEM (INJECTED)'],
  ['task_notification', null, 'SYSTEM (TASK NOTIFICATION)'],
  ['sdk_automation', null, 'SYSTEM (AUTOMATION)'],
  ['command_expansion', '/model', 'SYSTEM (COMMAND: /model)'],
  ['command_output', null, 'SYSTEM (COMMAND OUTPUT)'],
  ['harness_meta', 'reminder', 'SYSTEM (REMINDER)'],
  ['harness_meta', 'caveat', 'SYSTEM (CAVEAT)'],
  ['harness_meta', null, 'SYSTEM (META)'],
  ['interrupt_marker', null, 'SYSTEM (INTERRUPT)'],
  ['interrupt_marker', 'tool', 'SYSTEM (INTERRUPT)'],
  ['compact_summary', null, 'SYSTEM (COMPACTION)'],
  ['unclassified', null, 'SYSTEM (UNCLASSIFIED)'],
  ['system', 'turn_duration', 'SYSTEM (TURN DURATION)'],
  ['system', null, 'SYSTEM'],
  ['attachment_queued_human', null, 'SYSTEM (YOU)'],
  ['attachment_furniture', null, 'SYSTEM'],
]

describe('speakerFor — §3.3 label table', () => {
  it.each(cases)('kind=%s detail=%s → %s', (kind, detail, expectedLabel) => {
    expect(speakerFor(message({ authorship_kind: kind, authorship_detail: detail })).label).toBe(
      expectedLabel,
    )
  })
})

describe('speakerFor — accent reservation (§3.3)', () => {
  const DAWN_KINDS = new Set([
    'human_typed',
    'human_queued',
    'human_inferred',
    'attachment_queued_human',
  ])
  const DRAGONFLY_KINDS = new Set(['claude', 'dispatch', 'coordinator'])
  const ALL_KINDS = [...new Set(cases.map(([kind]) => kind))]

  it.each(ALL_KINDS)('accents %s correctly', (kind) => {
    const accent = speakerFor(message({ authorship_kind: kind, authorship_detail: null })).accent
    if (DAWN_KINDS.has(kind)) expect(accent).toBe('var(--dawn)')
    else if (DRAGONFLY_KINDS.has(kind)) expect(accent).toBe('var(--dragonfly)')
    else expect(accent).toBe('var(--mist)')
  })

  // Dawn-reservation property (global constraint): only human_* and attachment_queued_human may
  // ever take the dawn accent — every OTHER kind in the table must provably not take it.
  it('reserves dawn for human_* and attachment_queued_human only', () => {
    for (const kind of ALL_KINDS) {
      const accent = speakerFor(message({ authorship_kind: kind, authorship_detail: null })).accent
      expect(accent === 'var(--dawn)').toBe(DAWN_KINDS.has(kind))
    }
  })
})

describe('speakerFor — null-kind legacy fallback', () => {
  // A message not yet touched by the reparse backfill (spec §4 deploy window) has NO
  // authorship_kind: speakerFor must render the OLD type-derived YOU/CLAUDE/SYSTEM labels, not a
  // blank or an "unclassified" mislabel.
  it('falls back to the type-derived label when authorship_kind is null', () => {
    expect(speakerFor(message({ type: 'user', authorship_kind: null })).label).toBe('YOU')
    expect(speakerFor(message({ type: 'assistant', authorship_kind: null })).label).toBe('CLAUDE')
    expect(speakerFor(message({ type: 'system', authorship_kind: null })).label).toBe('SYSTEM')
    expect(
      speakerFor(
        message({ type: 'attachment', authorship_kind: null, blocks: [textBlock(0, 'x')] }),
      ).label,
    ).toBe('SYSTEM (YOU)')
    expect(
      speakerFor(message({ type: 'attachment', authorship_kind: null, blocks: [] })).label,
    ).toBe('SYSTEM')
  })

  it('falls back to the type-derived accent when authorship_kind is null', () => {
    expect(speakerFor(message({ type: 'user', authorship_kind: null })).accent).toBe(
      'var(--dawn)',
    )
    expect(speakerFor(message({ type: 'assistant', authorship_kind: null })).accent).toBe(
      'var(--dragonfly)',
    )
    expect(speakerFor(message({ type: 'system', authorship_kind: null })).accent).toBe(
      'var(--mist)',
    )
  })
})

describe('speakerFor DOM wiring', () => {
  it('reaches the eyebrow for a classified (non-null-kind) message', () => {
    const { container } = renderTurn(
      <MessageTurn
        message={message({ authorship_kind: 'dispatch', authorship_detail: null, timestamp: null })}
      />,
    )
    expect(container.querySelector('.turn-eyebrow')?.textContent).toBe('CLAUDE (DISPATCH)')
  })
})
