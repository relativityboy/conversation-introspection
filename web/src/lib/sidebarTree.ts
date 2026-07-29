/**
 * The sidebar tree-view toggle: show project structure as a tree (true) or flat (false).
 * Sticky across sessions via a single localStorage key; default flat.
 *
 * STATE MODEL (plan critique F4, binding): this hook is the ONE owner per sidebar —
 * the Sidebar component calls it exactly once and threads `[treeMode, setTreeMode]` down
 * as props to children that need to read or toggle the state. No component below the sidebar
 * calls this hook: two independent useState-from-localStorage instances do not sync, so
 * the toggle would flip while the tree silently never re-seeds — the exact seam bug this
 * design exists to prevent.
 */

import { useCallback, useState } from 'react'

const STORAGE_KEY = 'introspect.sidebarTree.v1'

// localStorage can throw on read (SecurityError in some private-mode configs) and on write
// (QuotaExceededError in Safari private mode). Both degrade to in-memory state rather than
// crashing the app — the toggle just doesn't persist across reloads in those environments.
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

export type UseSidebarTree = readonly [boolean, (value: boolean) => void]

export function useSidebarTree(): UseSidebarTree {
  const [treeMode, setTreeModeState] = useState(readStored)
  const setTreeMode = useCallback((value: boolean) => {
    setTreeModeState(value)
    writeStored(value)
  }, [])
  return [treeMode, setTreeMode]
}
