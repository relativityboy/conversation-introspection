import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { BlockOut, MessageOut } from '../src/api/types'
import { MessageTurn } from '../src/components/reader/MessageTurn'

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
    const { container } = render(<MessageTurn message={toolMsg} />)
    expect(container.querySelector('.tool-block')).not.toBeNull()
    expect(container.querySelector('.block-stub')).toBeNull()
    expect(container.querySelector('.thinking-glyph')).not.toBeNull()
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
