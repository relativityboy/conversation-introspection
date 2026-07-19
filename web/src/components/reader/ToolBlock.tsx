import type { CSSProperties } from 'react'
import { useState } from 'react'
import type { BlockOut } from '../../api/types'

// The threshold above which a collapsed tool row earns a byte-size hint. Below it the payload is
// cheap enough that the hint is just noise. Measured in real UTF-8 bytes via TextEncoder — a
// UTF-16 char count would under-report multibyte content while labeling the hint "KB".
const SIZE_HINT_THRESHOLD = 2048

const ROW_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  width: '100%',
  padding: 0,
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  textAlign: 'left',
  fontFamily: 'var(--mono)',
  fontSize: 11,
}

const BODY_STYLE: CSSProperties = {
  margin: '6px 0 0',
  padding: '10px 12px',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  background: 'var(--depth)',
  color: 'var(--moonpaper)',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  lineHeight: 1.5,
  whiteSpace: 'pre',
  overflowX: 'auto',
  maxHeight: 400,
  overflowY: 'auto',
}

// ToolBlock renders both tool_use and tool_result blocks as a collapsed, click-to-expand row.
// COLLAPSED BY DEFAULT, ALWAYS — a transcript is mostly prose, and unfurling every tool payload
// would drown it. The row is a real <button> so keyboard activation (Enter/Space) is native; the
// expanded <pre> is a sibling, never a child (block content can't live inside a button).
export function ToolBlock({ block }: { block: BlockOut }) {
  // NOTE(claude): virtuoso unmounts off-screen rows, so scrolling away resets this to collapsed
  // — within the collapsed-by-default contract, intentional (no lifted expansion state).
  const [expanded, setExpanded] = useState(false)

  const isResult = block.block_kind === 'tool_result'
  const isError = block.is_error === true
  const label = isResult ? '→ result' : `⌘ ${block.tool_name ?? 'tool'}`
  const text = block.text_content
  const byteSize = text ? new TextEncoder().encode(text).length : 0
  const sizeHint = byteSize > SIZE_HINT_THRESHOLD ? formatBytes(byteSize) : null

  return (
    <div
      className={`tool-block mono${isError ? ' tool-block-error' : ''}`}
      style={{ margin: '6px 0' }}
    >
      <button
        type="button"
        className="tool-block-row"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        style={{ ...ROW_STYLE, color: isError ? 'var(--ember)' : 'var(--mist)' }}
      >
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <span>{label}</span>
        {sizeHint && <span style={{ color: 'var(--mist)' }}>{sizeHint}</span>}
      </button>
      {expanded && (
        <pre className="tool-block-body" style={BODY_STYLE}>
          {text && text.length > 0 ? (
            text
          ) : (
            <span style={{ color: 'var(--mist)' }}>(no content)</span>
          )}
        </pre>
      )}
    </div>
  )
}

/** Human byte-size hint, e.g. "12.4 KB". Only ever called above the 2KB threshold, so the sub-KB
 * branch is a defensive floor rather than a live path.
 * NOTE(claude): binary KB (1024) here vs StatusBar's DECIMAL MB for the archive size — a
 * deliberate difference in conventions (text-buffer hint vs mockup-anchored disk size), not a
 * missing shared util. See the matching NOTE on StatusBar.formatMb. */
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  return `${(n / 1024).toFixed(1)} KB`
}
