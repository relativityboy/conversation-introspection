import type { CSSProperties } from 'react'
import type { BlockOut } from '../../api/types'

// The honest marker for a thinking block that carries NO text — the CLI mostly does not persist
// thinking content to the archive, so there is nothing to show and nothing to expand. It renders
// regardless of why text_content is empty; the point is to be truthful about the gap, not to
// imply hidden content. This label/glyph is the EMPTY-case only — a block that does carry real
// text_content renders its actual words instead (see the content branch below), never this.
const THINKING_LABEL = 'thinking occurred — content not persisted by the CLI'

const GLYPH_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 14,
  lineHeight: 1,
  color: 'var(--dragonfly)',
  opacity: 0.55,
  margin: '6px 0',
}

// Content-bearing thinking reuses the glyph's own dragonfly accent so the two registers (empty
// marker, real thinking text) read as one family, but at paragraph weight: a quieter foreground
// than body prose (MarkdownProse renders at full opacity) plus a thin low-opacity dragonfly rule
// on the left, so a thought is never mistaken for Claude's spoken text. `whiteSpace: 'pre-wrap'`
// preserves the model's own line breaks without wrapping the block in <pre> (thinking reads as a
// paragraph, not a code block).
const CONTENT_STYLE: CSSProperties = {
  margin: '6px 0',
  padding: '1px 0 1px 12px',
  borderLeft: '2px solid var(--dragonfly)',
  color: 'var(--dragonfly)',
  opacity: 0.75,
  lineHeight: 1.5,
  whiteSpace: 'pre-wrap',
}

export interface ThinkingGlyphProps {
  block: BlockOut
}

export function ThinkingGlyph({ block }: ThinkingGlyphProps) {
  const text = block.text_content

  // Content-bearing: render the real thinking text, visible immediately (unlike ToolBlock, this
  // is never collapsed — thinking is part of the read). Rendered as a plain text node, deliberately
  // NOT through MarkdownProse/react-markdown: thinking is plain text, not markdown, and must not
  // be reinterpreted as such.
  if (text) {
    return (
      <div
        className="thinking-content"
        role="group"
        aria-label="Claude's thinking"
        style={CONTENT_STYLE}
      >
        {text}
      </div>
    )
  }

  return (
    <div
      className="thinking-glyph mono"
      role="img"
      aria-label={THINKING_LABEL}
      title={THINKING_LABEL}
      style={GLYPH_STYLE}
    >
      ◌
    </div>
  )
}
