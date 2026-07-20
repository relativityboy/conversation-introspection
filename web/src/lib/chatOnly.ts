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

// The server's `_CHAT_ONLY_TYPES` mirror (routes/sessions.py): the message types that survive
// conversation-only mode. Kept as a set here so the ONE definition of "is this row part of the
// conversation-only view" lives in one place.
const CHAT_ONLY_TYPES = new Set(['user', 'assistant', 'attachment'])

/**
 * True when a message is shown in conversation-only mode. The single source of truth shared by
 * MessageTurn's row-level hiding AND the raw inspector's prev/next navigation, so the two can
 * never drift on WHICH rows count as "hidden": a `system`-type row is out (server-filtered), and
 * a zero-block attachment is harness furniture the reader hides even though its type qualifies
 * (MessageTurn returns null for it). Everything else the human said or pasted stays in.
 */
export function isChatOnlyVisible(message: {
  type: string
  blocks: readonly unknown[]
}): boolean {
  if (!CHAT_ONLY_TYPES.has(message.type)) return false
  if (message.type === 'attachment' && message.blocks.length === 0) return false
  return true
}

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
