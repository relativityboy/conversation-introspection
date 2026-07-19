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

function message(over: Partial<MessageOut> = {}): MessageOut {
  return {
    record_uuid: 'rec-1',
    parent_uuid: null,
    type: 'assistant',
    model: null,
    timestamp: '2026-07-19T14:03:00Z',
    blocks: [],
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

  it('exposes the honest aria-label and title', () => {
    render(<ThinkingGlyph />)
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
})

describe('ImageBlock', () => {
  it('renders a mono [image] chip', () => {
    const { container } = render(<ImageBlock />)
    expect(screen.getByText('[image]')).not.toBeNull()
    expect(container.querySelector('.image-chip')).not.toBeNull()
  })
})

describe('SubagentChip', () => {
  function renderChip(block: BlockOut, transcripts: TranscriptInfo[]) {
    return render(
      <MemoryRouter>
        <TranscriptsProvider value={{ sessionUuid: 'sess-uuid', transcripts }}>
          <SubagentChip block={block} />
        </TranscriptsProvider>
      </MemoryRouter>,
    )
  }

  it('renders a subagent pill and links to the transcript when matched', () => {
    renderChip(toolUse({ tool_use_id: 'tu-1' }), [transcript()])
    expect(screen.getByText('⑂ subagent · Explore')).not.toBeNull()
    const link = screen.getByRole('link', { name: /view transcript/ })
    expect(link.getAttribute('href')).toBe('/s/sess-uuid/a/a1b2c3')
  })

  it('truncates a long agent description to 60 chars', () => {
    const { container } = renderChip(toolUse({ tool_use_id: 'tu-1' }), [
      transcript({ agent_description: 'A'.repeat(100) }),
    ])
    const desc = container.querySelector('.subagent-desc')
    expect(desc?.textContent?.length).toBe(60)
    expect(desc?.textContent?.endsWith('…')).toBe(true)
  })

  it('degrades to a plain ToolBlock (no link) when no transcript matches', () => {
    const { container } = renderChip(toolUse({ tool_use_id: 'tu-nomatch', tool_name: 'Task' }), [])
    expect(container.querySelector('.tool-block')).not.toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('⌘ Task')).not.toBeNull()
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
