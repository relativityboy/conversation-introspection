import type { CSSProperties } from 'react'

// Small, mist-toned header pill. A real <button> with aria-pressed (not a checkbox) so it reads
// as one calm control in the mono metadata row and gets Enter/Space for free. Dragonfly ink +
// border when active. This is the ONLY place the phrase "conversation only" is painted -- the
// header meta row used to repeat it as a count suffix, which read as a second button appearing.
//
// NOTE(claude): both style objects must express the border with the SHORTHAND only. Spreading the
// shorthand and then overriding the `borderColor` LONGHAND made React drop the longhand on the way
// back to BASE_STYLE without re-applying the unchanged shorthand, leaving border-color cleared --
// so the resting outline painted black instead of var(--shore) after the first toggle. Don't
// reintroduce the mix; ChatOnlyToggle.test.tsx pins it.
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
  border: '1px solid var(--dragonfly)',
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
