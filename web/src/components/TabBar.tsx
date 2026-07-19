import type { CSSProperties } from 'react'
import { Link, matchPath, useLocation } from 'react-router-dom'

// The two reading-room views, presented as tabs. State is derived ENTIRELY from the route (no
// local state): `/search*` selects tab 1, any `/s/*` selects tab 2. Tab 2 has no target when no
// conversation is open, so it renders as a mist-toned, non-interactive label.
const TAB_BASE: CSSProperties = {
  fontFamily: 'var(--sans)',
  fontSize: 13,
  padding: '10px 2px',
  textDecoration: 'none',
  borderBottom: '2px solid transparent',
  cursor: 'pointer',
}

function tabStyle(active: boolean, disabled = false): CSSProperties {
  return {
    ...TAB_BASE,
    color: disabled ? 'var(--mist)' : active ? 'var(--dragonfly)' : 'var(--moonpaper)',
    borderBottomColor: active ? 'var(--dragonfly)' : 'transparent',
    cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.55 : 1,
  }
}

export function TabBar() {
  const location = useLocation()
  // end:false → prefix match, so `/s/abc` and `/s/abc/m/xyz` both yield the session uuid.
  const sessionMatch = matchPath({ path: '/s/:uuid', end: false }, location.pathname)
  const sessionUuid = sessionMatch?.params.uuid
  const searchActive = matchPath({ path: '/search', end: false }, location.pathname) !== null
  const sessionActive = Boolean(sessionUuid)

  return (
    <div
      role="tablist"
      aria-label="Reading room views"
      style={{
        display: 'flex',
        gap: 22,
        padding: '0 24px',
        borderBottom: '1px solid var(--shore)',
      }}
    >
      <Link
        role="tab"
        aria-selected={searchActive}
        to="/search"
        style={tabStyle(searchActive)}
      >
        Search all conversations
      </Link>

      {sessionUuid ? (
        <Link
          role="tab"
          aria-selected={sessionActive}
          to={`/s/${sessionUuid}`}
          style={tabStyle(sessionActive)}
        >
          Current conversation
        </Link>
      ) : (
        <span role="tab" aria-selected={false} aria-disabled="true" style={tabStyle(false, true)}>
          Current conversation
        </span>
      )}
    </div>
  )
}
