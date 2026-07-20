import type { CSSProperties } from 'react'

// Small, mist-toned header pill. A real <button> with aria-pressed (not a checkbox) so it reads
// as one calm control in the mono metadata row and gets Enter/Space for free. Dragonfly ink +
// border when active, mirroring the "· conversation only" suffix the header shows alongside it.
const BASE_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  background: 'none',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  padding: '2px 8px',
  cursor: 'pointer',
  color: 'var(--mist)',
}

const ACTIVE_STYLE: CSSProperties = {
  ...BASE_STYLE,
  color: 'var(--dragonfly)',
  borderColor: 'var(--dragonfly)',
}

export interface ChatOnlyToggleProps {
  chatOnly: boolean
  setChatOnly: (value: boolean) => void
}

/** The header control for conversation-only mode. State is OWNED by the page (via `useChatOnly`)
 * and passed in — this component is purely presentational so the header and the reader body can
 * never fall out of sync (plan critique F4). */
export function ChatOnlyToggle({ chatOnly, setChatOnly }: ChatOnlyToggleProps) {
  return (
    <button
      type="button"
      className="chat-only-toggle mono"
      aria-pressed={chatOnly}
      onClick={() => setChatOnly(!chatOnly)}
      style={chatOnly ? ACTIVE_STYLE : BASE_STYLE}
    >
      conversation only
    </button>
  )
}
