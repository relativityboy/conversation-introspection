import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { BlockOut, MessageOut, TranscriptInfo } from '../src/api/types'
import { ImageBlock } from '../src/components/reader/ImageBlock'
import { MessageTurn } from '../src/components/reader/MessageTurn'
import { SubagentChip } from '../src/components/reader/SubagentChip'
import { ThinkingGlyph } from '../src/components/reader/ThinkingGlyph'
import { ToolBlock } from '../src/components/reader/ToolBlock'
import { TranscriptsProvider } from '../src/components/reader/transcripts-context'

// Block renderers are tested un-virtualized (plain render): class names, DOM order, aria, and
// expand/collapse interactions are all real in jsdom; nothing here depends on measured layout.

function toolUse(over: Partial<BlockOut> = {}): BlockOut {
  return {
    block_index: 0,
    block_kind: 'tool_use',
    text_content: null,
    tool_name: 'Bash',
    tool_use_id: 'tu-1',
    is_error: null,
    ...over,
  }
}

function toolResult(over: Partial<BlockOut> = {}): BlockOut {
  return {
    block_index: 0,
    block_kind: 'tool_result',
    text_content: 'some output',
    tool_name: null,
    tool_use_id: 'tu-1',
    is_error: null,
    ...over,
  }
}

function thinkingBlock(over: Partial<BlockOut> = {}): BlockOut {
  return {
    block_index: 0,
    block_kind: 'thinking',
    text_content: null,
    tool_name: null,
    tool_use_id: null,
    is_error: null,
    ...over,
  }
}

function message(over: Partial<MessageOut> = {}): MessageOut {
  return {
    record_uuid: 'rec-1',
    parent_uuid: null,
    type: 'assistant',
    model: null,
    timestamp: '2026-07-19T14:03:00Z',
    blocks: [],
    authorship_kind: null,
    authorship_basis: null,
    authorship_detail: null,
    ...over,
  }
}

function transcript(over: Partial<TranscriptInfo> = {}): TranscriptInfo {
  return {
    id: 2,
    kind: 'subagent',
    agent_hex_id: 'a1b2c3',
    agent_type: 'Explore',
    agent_description: 'search the codebase for X',
    parent_tool_use_id: 'tu-1',
    ...over,
  }
}

describe('ToolBlock', () => {
  it('is collapsed by default — no body is rendered', () => {
    const { container } = render(<ToolBlock block={toolResult()} />)
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false')
    expect(container.querySelector('.tool-block-body')).toBeNull()
  })

  it('expands to show the content when the row is clicked', () => {
    const { container } = render(<ToolBlock block={toolResult({ text_content: 'hi there' })} />)
    fireEvent.click(screen.getByRole('button'))
    const body = container.querySelector('.tool-block-body')
    expect(body).not.toBeNull()
    expect(body?.textContent).toContain('hi there')
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true')
  })

  it('expands on keyboard Enter (real button is keyboard-safe)', async () => {
    const user = userEvent.setup()
    const { container } = render(<ToolBlock block={toolResult({ text_content: 'keyed open' })} />)
    screen.getByRole('button').focus()
    await user.keyboard('{Enter}')
    expect(container.querySelector('.tool-block-body')?.textContent).toContain('keyed open')
  })

  it('labels a tool_use row with ⌘ and the tool name', () => {
    render(<ToolBlock block={toolUse({ tool_name: 'Bash' })} />)
    expect(screen.getByText('⌘ Bash')).not.toBeNull()
  })

  it('labels a tool_result row as → result', () => {
    render(<ToolBlock block={toolResult()} />)
    expect(screen.getByText('→ result')).not.toBeNull()
  })

  it('gives an error result the ember error class', () => {
    const { container } = render(<ToolBlock block={toolResult({ is_error: true })} />)
    expect(container.querySelector('.tool-block-error')).not.toBeNull()
  })

  it('shows a byte-size hint for content larger than 2KB', () => {
    const big = 'x'.repeat(3072)
    render(<ToolBlock block={toolResult({ text_content: big })} />)
    expect(screen.getByText(/KB/)).not.toBeNull()
  })

  it('shows no size hint for small content', () => {
    render(<ToolBlock block={toolResult({ text_content: 'tiny' })} />)
    expect(screen.queryByText(/KB/)).toBeNull()
  })

  it('shows a (no content) marker when expanded with empty text', () => {
    render(<ToolBlock block={toolResult({ text_content: null })} />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('(no content)')).not.toBeNull()
  })
})

describe('ThinkingGlyph', () => {
  const LABEL = 'thinking occurred — content not persisted by the CLI'

  it('exposes the honest aria-label and title for an empty-text block', () => {
    render(<ThinkingGlyph block={thinkingBlock({ text_content: '' })} />)
    const glyph = screen.getByLabelText(LABEL)
    expect(glyph.getAttribute('title')).toBe(LABEL)
  })

  it('renders even for an empty thinking block (the honest marker)', () => {
    const msg = message({
      blocks: [
        {
          block_index: 0,
          block_kind: 'thinking',
          text_content: '',
          tool_name: null,
          tool_use_id: null,
          is_error: null,
        },
      ],
    })
    render(<MessageTurn message={msg} />)
    expect(screen.getByLabelText(LABEL)).not.toBeNull()
  })

  it('renders the thinking text in place of the circle when text_content is non-empty', () => {
    render(<ThinkingGlyph block={thinkingBlock({ text_content: 'considering the tradeoffs' })} />)
    expect(screen.getByText('considering the tradeoffs')).not.toBeNull()
    expect(screen.queryByText('◌')).toBeNull()
  })

  it('omits THINKING_LABEL and exposes a distinct a11y name for content-bearing thinking', () => {
    render(<ThinkingGlyph block={thinkingBlock({ text_content: 'considering the tradeoffs' })} />)
    expect(screen.queryByLabelText(LABEL)).toBeNull()
    expect(screen.getByLabelText("Claude's thinking")).not.toBeNull()
  })

  it('preserves line breaks in multiline thinking text', () => {
    const multiline = 'first line\nsecond line\nthird line'
    render(<ThinkingGlyph block={thinkingBlock({ text_content: multiline })} />)
    const el = screen.getByLabelText("Claude's thinking")
    expect(el.textContent).toBe(multiline)
    expect((el as HTMLElement).style.whiteSpace).toMatch(/pre/)
  })

  it('renders thinking text as plain text, never through a markdown pipeline', () => {
    render(<ThinkingGlyph block={thinkingBlock({ text_content: '**not bold** and `not code`' })} />)
    const el = screen.getByLabelText("Claude's thinking")
    expect(el.textContent).toBe('**not bold** and `not code`')
    expect(el.querySelector('strong')).toBeNull()
    expect(el.querySelector('code')).toBeNull()
  })
})

describe('ImageBlock', () => {
  it('renders a mono [image] chip', () => {
    const { container } = render(<ImageBlock />)
    expect(screen.getByText('[image]')).not.toBeNull()
    expect(container.querySelector('.image-chip')).not.toBeNull()
  })
})

describe('SubagentChip', () => {
  // renderFallback defaults true here (matching the reader's `all`-view behavior) so the existing
  // matched-transcript cases below don't need to think about it; the dedicated renderFallback
  // tests pass it explicitly.
  function renderChip(
    block: BlockOut,
    transcripts: TranscriptInfo[],
    initialEntry = '/',
    renderFallback = true,
  ) {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <TranscriptsProvider value={{ sessionUuid: 'sess-uuid', transcripts }}>
          <SubagentChip block={block} renderFallback={renderFallback} />
        </TranscriptsProvider>
      </MemoryRouter>,
    )
  }

  // Task T10: the drill-in also carries the reader's current category selection (readSelection
  // defaults to the `chat` preset when the URL has neither `?select=` nor `?view=`), so a bare
  // '/' entry now writes the pretty `?view=chat` rather than no param at all — writeSelection
  // never omits it (see viewMode.ts).
  it('renders a subagent pill and links to the transcript when matched', () => {
    renderChip(toolUse({ tool_use_id: 'tu-1' }), [transcript()])
    expect(screen.getByText('⑂ subagent · Explore')).not.toBeNull()
    const link = screen.getByRole('link', { name: /view transcript/ })
    expect(link.getAttribute('href')).toBe('/s/sess-uuid/a/a1b2c3?view=chat')
  })

  // Task 9: the "view transcript →" drill-in is a deep link — it must carry the current project
  // filter, read live from the URL (SubagentChip has no other route context available to it).
  it('preserves ?projects= on the "view transcript" link', () => {
    renderChip(toolUse({ tool_use_id: 'tu-1' }), [transcript()], '/s/sess-uuid?projects=alpha,mid')
    const link = screen.getByRole('link', { name: /view transcript/ })
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (same as every
    // other writeProjects-built link in this app — see Sidebar.test.tsx for the full note).
    expect(link.getAttribute('href')).toBe('/s/sess-uuid/a/a1b2c3?projects=alpha%2Cmid&view=chat')
  })

  // Task T10: the selection half of the same carry-through — a custom (non-preset) combination
  // writes `?select=` instead, and a preset writes the pretty `?view=` even when it's the only
  // param present.
  it('carries the current category selection onto the "view transcript" link', () => {
    renderChip(toolUse({ tool_use_id: 'tu-1' }), [transcript()], '/s/sess-uuid?view=all')
    const link = screen.getByRole('link', { name: /view transcript/ })
    expect(link.getAttribute('href')).toBe('/s/sess-uuid/a/a1b2c3?view=all')
  })

  it('writes ?select= for a custom (non-preset) category combination', () => {
    renderChip(
      toolUse({ tool_use_id: 'tu-1' }),
      [transcript()],
      '/s/sess-uuid?select=you-chat,tool-traffic',
    )
    const link = screen.getByRole('link', { name: /view transcript/ })
    const href = link.getAttribute('href') ?? ''
    const [path, qs] = href.split('?')
    expect(path).toBe('/s/sess-uuid/a/a1b2c3')
    expect(new Set(new URLSearchParams(qs).get('select')?.split(','))).toEqual(
      new Set(['you-chat', 'tool-traffic']),
    )
  })

  it('truncates a long agent description to 60 chars', () => {
    const { container } = renderChip(toolUse({ tool_use_id: 'tu-1' }), [
      transcript({ agent_description: 'A'.repeat(100) }),
    ])
    const desc = container.querySelector('.subagent-desc')
    expect(desc?.textContent?.length).toBe(60)
    expect(desc?.textContent?.endsWith('…')).toBe(true)
  })

  it('degrades to a plain ToolBlock (no link) when no transcript matches and renderFallback is true', () => {
    const { container } = renderChip(toolUse({ tool_use_id: 'tu-nomatch', tool_name: 'Task' }), [])
    expect(container.querySelector('.tool-block')).not.toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('⌘ Task')).not.toBeNull()
  })

  // Task 6: the per-view block gate (MessageTurn) suppresses the ordinary-tool fallback outside
  // `all` by passing renderFallback=false — SubagentChip itself owns that suppression.
  it('renders nothing when no transcript matches and renderFallback is false', () => {
    const { container } = renderChip(
      toolUse({ tool_use_id: 'tu-nomatch', tool_name: 'Task' }),
      [],
      '/',
      false,
    )
    expect(container.querySelector('.tool-block')).toBeNull()
    expect(container.textContent).toBe('')
  })

  it('still renders a resolved chip when renderFallback is false — only the fallback is suppressed', () => {
    renderChip(toolUse({ tool_use_id: 'tu-1' }), [transcript()], '/', false)
    expect(screen.getByText('⑂ subagent · Explore')).not.toBeNull()
    expect(screen.getByRole('link', { name: /view transcript/ })).not.toBeNull()
  })
})

describe('MessageTurn block dispatch', () => {
  it('routes an unknown block kind to a mono [kind] chip (forward-tolerant)', () => {
    const msg = message({
      blocks: [
        {
          block_index: 0,
          block_kind: 'video',
          text_content: null,
          tool_name: null,
          tool_use_id: null,
          is_error: null,
        },
      ],
    })
    render(<MessageTurn message={msg} />)
    expect(screen.getByText('[video]')).not.toBeNull()
  })

  it('routes an image block to the [image] chip', () => {
    const msg = message({
      blocks: [
        {
          block_index: 0,
          block_kind: 'image',
          text_content: null,
          tool_name: null,
          tool_use_id: null,
          is_error: null,
        },
      ],
    })
    render(<MessageTurn message={msg} />)
    expect(screen.getByText('[image]')).not.toBeNull()
  })
})
