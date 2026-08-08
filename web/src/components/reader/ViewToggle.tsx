import type { CSSProperties } from 'react'
import type { ViewMode } from '../../lib/viewMode'

// Successor of the retired boolean toggle: a pressed/unpressed pill can't represent three states,
// so this renders three individually-clickable segments -- "chat · chat+harness · all" -- in the
// same mist-toned mono vocabulary as the control it replaces.
//
// NOTE(claude): both style objects must express the border with the SHORTHAND only, per the
// retired toggle's original regression note -- spreading the shorthand and then overriding a
// LONGHAND (e.g. `borderColor`) makes React drop the longhand on the next render if the new style
// object doesn't repeat it, since React diffs style objects key-by-key rather than reapplying
// unchanged shorthands. Keep the active/base style objects each carrying only the shorthand.
const WRAP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  border: '1px solid var(--shore)',
  borderRadius: 6,
  padding: '2px 8px',
  gap: 4,
}

const BASE_STYLE: CSSProperties = {
  fontFamily: 'inherit',
  fontSize: 'inherit',
  letterSpacing: 'inherit',
  lineHeight: 'inherit',
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: 'var(--mist)',
}

const ACTIVE_STYLE: CSSProperties = {
  ...BASE_STYLE,
  color: 'var(--dragonfly)',
}

const SEPARATOR_STYLE: CSSProperties = { color: 'var(--mist)' }

export interface ViewToggleProps {
  view: ViewMode
  setView: (view: ViewMode) => void
}

/** The header control for the reader's three-state view (authorship spec §5): `chat` (pure
 * conversation, default) / `chat+harness` (adds tool dispatch/skill structure, still hides tool
 * results) / `all` (everything). State is OWNED by the page (via `useViewMode`) and passed in --
 * this component is purely presentational so the header and the reader body can never fall out of
 * sync (plan critique F4, carried over from the retired toggle). */
export function ViewToggle({ view, setView }: ViewToggleProps) {
  return (
    <span className="view-toggle mono" role="group" aria-label="view" style={WRAP_STYLE}>
      <ViewOption value="chat" label="chat" view={view} setView={setView} />
      <span style={SEPARATOR_STYLE}> · </span>
      <ViewOption value="chat-harness" label="chat+harness" view={view} setView={setView} />
      <span style={SEPARATOR_STYLE}> · </span>
      <ViewOption value="all" label="all" view={view} setView={setView} />
    </span>
  )
}

function ViewOption({ value, label, view, setView }: ViewToggleProps & { value: ViewMode; label: string }) {
  const active = view === value
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => setView(value)}
      style={active ? ACTIVE_STYLE : BASE_STYLE}
    >
      {label}
    </button>
  )
}
