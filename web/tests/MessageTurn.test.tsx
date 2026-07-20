import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { BlockOut, MessageOut, TranscriptInfo } from '../src/api/types'
import { MessageTurn } from '../src/components/reader/MessageTurn'
import { TranscriptsProvider } from '../src/components/reader/transcripts-context'

// MessageTurn is deliberately tested UN-virtualized (plain render, no Virtuoso) — jsdom has no
// layout engine, so these assertions stay honest: class names, DOM order, and markdown output
// are all real; nothing depends on measured heights.

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
    const { container } = render(<MessageTurn message={message({ type: 'user' })} />)
    expect(turnOf(container).classList.contains('turn-user')).toBe(true)
  })

  it('marks assistant turns with the dragonfly accent class', () => {
    const { container } = render(<MessageTurn message={message({ type: 'assistant' })} />)
    expect(turnOf(container).classList.contains('turn-assistant')).toBe(true)
  })

  it('marks system turns with the mist accent class', () => {
    const { container } = render(<MessageTurn message={message({ type: 'system' })} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
  })

  it('treats unknown record types as system-accented', () => {
    const { container } = render(<MessageTurn message={message({ type: 'summary' })} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
  })
})

describe('eyebrow', () => {
  it('renders "SPEAKER · HH:MM" with the local time', () => {
    const iso = '2026-07-19T14:03:00Z'
    const { container } = render(
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
    const a = render(<MessageTurn message={message({ type: 'assistant' })} />)
    expect(a.container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^CLAUDE · /)
    const s = render(<MessageTurn message={message({ type: 'system' })} />)
    expect(s.container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM · /)
  })

  it('omits the time (no separator) when the timestamp is absent', () => {
    const { container } = render(<MessageTurn message={message({ timestamp: null })} />)
    expect(container.querySelector('.turn-eyebrow')?.textContent).toBe('YOU')
  })
})

describe('block ordering and dispatch', () => {
  it('renders blocks in block_index order even when the array is shuffled', () => {
    const shuffled = message({
      blocks: [textBlock(2, 'third'), textBlock(0, 'first'), textBlock(1, 'second')],
    })
    const { container } = render(<MessageTurn message={shuffled} />)
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
    // MemoryRouter is still required, though: SubagentChip (Task 9) reads the current
    // ?projects= via useSearchParams() unconditionally, before the no-match check runs.
    const { container } = render(
      <MemoryRouter>
        <MessageTurn message={toolMsg} />
      </MemoryRouter>,
    )
    expect(container.querySelector('.tool-block')).not.toBeNull()
    expect(container.querySelector('.block-stub')).toBeNull()
    expect(container.querySelector('.thinking-glyph')).not.toBeNull()
  })
})

describe('conversation-only block hiding (chatOnly prop)', () => {
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

  it('hides tool_use and tool_result blocks but keeps text/thinking/image', () => {
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
    const { container } = render(
      <MemoryRouter>
        <MessageTurn message={msg} chatOnly />
      </MemoryRouter>,
    )
    // tool_use (as ToolBlock, no transcript match) and tool_result both vanish.
    expect(container.querySelector('.tool-block')).toBeNull()
    // conversational blocks remain.
    expect(container.textContent).toContain('kept text')
    expect(container.querySelector('.thinking-glyph')).not.toBeNull()
    expect(container.textContent).toContain('[image]')
  })

  it('makes the subagent chip disappear with its tool_use block (ledger #7, intended)', () => {
    const dispatch: TranscriptInfo = {
      id: 2,
      kind: 'subagent',
      agent_hex_id: 'a1b2c3',
      agent_type: 'Explore',
      agent_description: null,
      parent_tool_use_id: 'tu-1',
    }
    const msg = message({ blocks: [toolBlock(0)] })
    render(
      <MemoryRouter>
        <TranscriptsProvider value={{ sessionUuid: 'sess', transcripts: [dispatch] }}>
          <MessageTurn message={msg} chatOnly />
        </TranscriptsProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByRole('link', { name: /view transcript/ })).toBeNull()
    expect(screen.queryByText(/subagent/)).toBeNull()
  })

  it('renders tool blocks normally when chatOnly is off (default, no prop)', () => {
    const msg = message({ blocks: [toolBlock(0, { block_kind: 'tool_result', text_content: 'x' })] })
    const { container } = render(
      <MemoryRouter>
        <MessageTurn message={msg} />
      </MemoryRouter>,
    )
    expect(container.querySelector('.tool-block')).not.toBeNull()
  })
})

// Task P4-F1: a block-bearing attachment is a rescued human queued-command — labelled
// SYSTEM (YOU), dawn (user) accent — while a zero-block attachment is harness furniture that
// keeps the plain SYSTEM treatment in full mode and vanishes entirely under chatOnly.
describe('attachment voice (rescued queued commands)', () => {
  function accentOf(container: HTMLElement): string {
    const inner = container.querySelector<HTMLElement>('.message-turn > div')
    expect(inner).not.toBeNull()
    return (inner as HTMLElement).style.borderLeft
  }

  it('labels a block-bearing attachment SYSTEM (YOU) with the dawn accent', () => {
    const msg = message({ type: 'attachment', blocks: [textBlock(0, 'queued human words')] })
    const { container } = render(<MessageTurn message={msg} />)
    expect(turnOf(container).classList.contains('turn-attachment')).toBe(true)
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM \(YOU\) · /)
    expect(accentOf(container)).toContain('var(--dawn)')
    expect(container.textContent).toContain('queued human words')
  })

  it('keeps a zero-block attachment as plain SYSTEM in full mode (mist accent)', () => {
    const msg = message({ type: 'attachment', blocks: [] })
    const { container } = render(<MessageTurn message={msg} />)
    expect(turnOf(container).classList.contains('turn-system')).toBe(true)
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM · /)
    expect(accentOf(container)).toContain('var(--mist)')
  })

  it('hides a zero-block attachment entirely when chatOnly is on', () => {
    const msg = message({ type: 'attachment', blocks: [] })
    const { container } = render(<MessageTurn message={msg} chatOnly />)
    expect(container.querySelector('.message-turn')).toBeNull()
    expect(container.textContent).toBe('')
  })

  it('keeps a block-bearing attachment visible when chatOnly is on', () => {
    const msg = message({ type: 'attachment', blocks: [textBlock(0, 'still a human turn')] })
    const { container } = render(<MessageTurn message={msg} chatOnly />)
    expect(container.querySelector('.message-turn')).not.toBeNull()
    expect(container.querySelector('.turn-eyebrow')?.textContent).toMatch(/^SYSTEM \(YOU\) · /)
    expect(container.textContent).toContain('still a human turn')
  })
})

describe('markdown prose', () => {
  it('renders bold and inline code', () => {
    const { container } = render(
      <MessageTurn message={message({ blocks: [textBlock(0, '**bold** and `inline`')] })} />,
    )
    expect(container.querySelector('.markdown-prose strong')?.textContent).toBe('bold')
    expect(container.querySelector('.markdown-prose code')?.textContent).toBe('inline')
  })

  it('never mounts raw HTML — a <script> in markdown does not reach the DOM', () => {
    const hostile = message({
      blocks: [textBlock(0, 'before\n\n<script>window.pwned = true</script>\n\nafter')],
    })
    const { container } = render(<MessageTurn message={hostile} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('before')
    expect(container.textContent).toContain('after')
  })

  it('renders an unknown fence language (```notalang) as a plain code block without throwing', () => {
    const fenced = message({
      blocks: [textBlock(0, '```notalang\nweird ~~ stuff <<>>\n```')],
    })
    const { container } = render(<MessageTurn message={fenced} />)
    const code = container.querySelector('.markdown-prose pre code')
    expect(code).not.toBeNull()
    expect(code?.textContent).toContain('weird ~~ stuff <<>>')
  })

  it('highlights a known fence language with hljs token spans', () => {
    const fenced = message({
      blocks: [textBlock(0, '```js\nconst x = "still water"\n```')],
    })
    const { container } = render(<MessageTurn message={fenced} />)
    expect(container.querySelector('.markdown-prose pre code .hljs-keyword')).not.toBeNull()
  })
})
