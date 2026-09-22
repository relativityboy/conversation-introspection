import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import type { MemoryOut } from '../../api/types'
import { MarkdownProse } from '../reader/MarkdownProse'

export interface MemoryCardProps {
  memory: MemoryOut
}

const CARD_STYLE: CSSProperties = {
  border: '1px solid var(--shore)',
  borderRadius: 8,
  padding: '12px 14px',
  marginBottom: 10,
  background: 'var(--surface)',
}

const HEADER_ROW_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  flexWrap: 'wrap',
}

const BADGE_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  color: 'var(--mist)',
  border: '1px solid var(--shore)',
  borderRadius: 999,
  padding: '2px 8px',
  whiteSpace: 'nowrap',
}

const NAME_BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--serif)',
  fontSize: 16,
  fontWeight: 600,
  color: 'var(--moonpaper)',
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  textAlign: 'left',
}

const DESC_STYLE: CSSProperties = {
  fontFamily: 'var(--sans)',
  fontSize: 13,
  color: 'var(--moonpaper)',
  margin: '6px 0',
}

const MIST_DESC_STYLE: CSSProperties = { ...DESC_STYLE, color: 'var(--mist)' }

const NOTE_STYLE: CSSProperties = {
  fontFamily: 'var(--sans)',
  fontSize: 12,
  color: 'var(--mist)',
  margin: '4px 0',
}

// Reset so the button reads as a quiet mono chip, but clickable -- same convention as
// SessionPage's SESSION_ID_STYLE. maxWidth + ellipsis: a long filename must never force the card
// (and therefore the page) to scroll horizontally.
const COPY_BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  color: 'var(--mist)',
  background: 'none',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  padding: '2px 8px',
  cursor: 'pointer',
  maxWidth: 320,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  verticalAlign: 'middle',
  display: 'inline-block',
}

// One card per memory file: a type badge, the name (click toggles the body), the description (or
// a mist "no description"), an "unreadable: <error>" line when the file couldn't be fully read,
// and a copy chip for the file path. NOTE(claude): the copy chip duplicates SessionPage's
// SessionIdChip pattern (useRef timer + unmount cleanup) rather than extracting a shared
// component -- per repo precedent (see the comment at components/ProjectTree.tsx:250-251), a
// ~30-line copy-and-flash widget isn't worth a shared abstraction for its second use.
export function MemoryCard({ memory }: MemoryCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    }
  }, [])

  function copy() {
    void navigator.clipboard?.writeText(memory.path).catch(() => {})
    setCopied(true)
    if (timerRef.current !== null) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      setCopied(false)
    }, 1600)
  }

  return (
    <article style={CARD_STYLE}>
      <div style={HEADER_ROW_STYLE}>
        <span style={BADGE_STYLE}>{memory.type ?? 'untyped'}</span>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((e) => !e)}
          style={NAME_BUTTON_STYLE}
        >
          {memory.name}
        </button>
      </div>
      <p style={memory.description ? DESC_STYLE : MIST_DESC_STYLE}>
        {memory.description ?? 'no description'}
      </p>
      {memory.error !== null && <p style={NOTE_STYLE}>unreadable: {memory.error}</p>}
      <span>
        <button
          type="button"
          onClick={copy}
          title={memory.path}
          aria-label={`copy path for ${memory.filename}`}
          style={COPY_BUTTON_STYLE}
        >
          {memory.filename}
        </button>
        {copied && (
          <span style={{ color: 'var(--dragonfly)', marginLeft: 6, fontSize: 11 }}>copied</span>
        )}
      </span>
      {expanded && (
        <div style={{ marginTop: 10 }}>
          {memory.body === null ? (
            <p style={NOTE_STYLE}>body unavailable</p>
          ) : (
            <MarkdownProse markdown={memory.body} />
          )}
        </div>
      )}
    </article>
  )
}
