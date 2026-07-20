/**
 * The conversation-only reader mode: hide tool_use / tool_result blocks (and the subagent chips
 * that ride them) and, when the seed goes through the API, filter the server response to
 * `type IN ('user','assistant','attachment')`. Sticky across sessions and readers via a single
 * localStorage key; default off.
 *
 * STATE MODEL (plan critique F4, binding): this hook is the ONE owner per reader page — each
 * `SessionPage` / `SubagentPage` calls it exactly once and threads `[chatOnly, setChatOnly]` down
 * as props (header toggle + reader body + 404 recovery all read the SAME state). No component
 * below the page calls this hook: two independent useState-from-localStorage instances do not
 * sync, so the header would flip while the reader silently never re-seeds — the exact seam bug
 * this design exists to prevent.
 */

import { useCallback, useState } from 'react'

const STORAGE_KEY = 'introspect.chatOnly.v1'

// localStorage can throw on read (SecurityError in some private-mode configs) and on write
// (QuotaExceededError in Safari private mode). Both degrade to in-memory state rather than
// crashing the reader — the toggle just doesn't persist across reloads in those environments.
function readStored(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function writeStored(value: boolean): void {
  try {
    if (value) {
      window.localStorage.setItem(STORAGE_KEY, '1')
    } else {
      // Remove rather than write "0": absent === off, so a cleared flag leaves no stale key.
      window.localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    // Private mode / storage disabled: keep the in-memory state, skip persistence.
  }
}

export type UseChatOnly = readonly [boolean, (value: boolean) => void]

export function useChatOnly(): UseChatOnly {
  const [chatOnly, setChatOnlyState] = useState(readStored)
  const setChatOnly = useCallback((value: boolean) => {
    setChatOnlyState(value)
    writeStored(value)
  }, [])
  return [chatOnly, setChatOnly]
}
