/**
 * The reader's three-state view mode (authorship spec §5), successor to the old boolean
 * "conversation only" toggle (retired): `chat` (pure human/Claude conversation, the default),
 * `chat-harness` (adds tool dispatch/skill structure, still hides tool_result bodies), `all`
 * (everything, including tool results). Sticky across sessions and readers via a single
 * localStorage key.
 *
 * STATE MODEL (carried over from the retired toggle's plan-critique F4 rule, still binding): this
 * hook is the ONE owner per reader page — each `SessionPage` / `SubagentPage` calls it exactly
 * once and threads `{view, setView}` down as props (header toggle + reader body + 404 recovery
 * all read the SAME state). No component below the page calls this hook: two independent
 * useState-from-localStorage instances do not sync, so the header would flip while the reader
 * silently never re-seeds — the exact seam bug this design exists to prevent.
 */

import { useCallback, useState } from 'react'
import type { BlockOut, MessageOut } from '../api/types'

export type ViewMode = 'chat' | 'chat-harness' | 'all'

const STORAGE_KEY = 'introspect.view.v1'

// The retired boolean toggle's storage key. Zero-legacy: never READ (a stale '1' left over from
// before this migration must not resurrect a filtered reading), and REMOVED on first write to
// this hook's own key so it can never be misread by old code or a stale reload.
const LEGACY_STORAGE_KEY = 'introspect.chatOnly.v1'

const DEFAULT_VIEW: ViewMode = 'chat'

// Literal copy of the server's authorship kinds that read as a human/Claude conversational turn
// under `view=chat` — source of truth: server `schema/authorship.py` `CHAT_KINDS`. Change both
// together.
export const CHAT_KINDS: ReadonlySet<string> = new Set([
  'human_typed',
  'human_queued',
  'human_inferred',
  'claude',
  'attachment_queued_human',
  'interrupt_marker',
  'dispatch',
  'coordinator',
])

// The legacy §14.4 "conversation only" type set. Mirrors server `routes/sessions.py`
// `_LEGACY_TYPES` — doubles there (and here) as the NULL-tolerance fallback for rows not yet
// backfilled with an `authorship_kind` (migrate→reparse deploy window, spec §4/§5).
const LEGACY_TYPES = new Set(['user', 'assistant', 'attachment'])

// Mirror of the server's known-kinds list (`routes/sessions.py` `_KNOWN_BLOCK_KINDS`): unknown
// block kinds render a visible UnknownChip client-side, so they count as content.
const KNOWN_BLOCK_KINDS = new Set(['text', 'thinking', 'tool_use', 'tool_result', 'image'])

function blockShowsContent(block: BlockOut): boolean {
  if (block.block_kind === 'text') return block.text_content !== null && block.text_content !== ''
  if (block.block_kind === 'image') return true
  return !KNOWN_BLOCK_KINDS.has(block.block_kind)
}

// Spec §4: a row is visible in a filtered view only when at least one block renders content there
// — non-empty text, an image, or an unknown kind. thinking (◌), tool blocks, and empty text don't
// count. Layered on top of the kind/type gate by `isVisibleInView` for both `chat` and
// `chat-harness`; `all` never applies this rule (server `_view_filter` mirrors the same split).
function proseVisible(message: MessageOut): boolean {
  return message.blocks.some(blockShowsContent)
}

/**
 * Client mirror of the server's `_view_filter` (`server/src/introspect/api/routes/sessions.py`,
 * using `CHAT_KINDS` from `server/src/introspect/schema/authorship.py`) — the SAME predicate, so
 * the rows the reader shows/hides and the rows the raw inspector's prev/next skip can never drift.
 * NULL-tolerant: a message not yet backfilled with an `authorship_kind` (migrate→reparse deploy
 * window) degrades to the legacy type+content rule rather than vanishing from every filtered view.
 */
export function isVisibleInView(message: MessageOut, view: ViewMode): boolean {
  if (view === 'all') return true

  // `== null` (not `===`): tolerates `undefined` as well as `null` at this boundary — the wire
  // contract always sends an explicit `null`, but this keeps the predicate defensive rather than
  // silently mis-filtering a malformed/partial payload.
  const kind = message.authorship_kind
  const legacyFallback = kind == null && LEGACY_TYPES.has(message.type)

  const kindOk =
    view === 'chat'
      ? (kind != null && CHAT_KINDS.has(kind)) || legacyFallback
      : // chat-harness: everything except an explicit tool_result kind, still gated to the
        // legacy TYPE set — mirrors server `_view_filter`'s
        // `(kind IS NULL OR kind != 'tool_result') AND type IN legacy_types` (the server's
        // redundant `OR legacy_fallback` term is subsumed here: when kind is null the first
        // clause is already true, so it never changes the result).
        (kind == null || kind !== 'tool_result') && LEGACY_TYPES.has(message.type)

  return kindOk && proseVisible(message)
}

function readStored(): ViewMode {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    if (value === 'chat' || value === 'chat-harness' || value === 'all') return value
    return DEFAULT_VIEW
  } catch {
    return DEFAULT_VIEW
  }
}

function writeStored(value: ViewMode): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, value)
    // Zero-legacy: drop the retired boolean key now that a real view has been chosen explicitly.
    window.localStorage.removeItem(LEGACY_STORAGE_KEY)
  } catch {
    // Private mode / storage disabled: keep the in-memory state, skip persistence.
  }
}

export function useViewMode(): { view: ViewMode; setView: (view: ViewMode) => void } {
  const [view, setViewState] = useState(readStored)
  const setView = useCallback((value: ViewMode) => {
    setViewState(value)
    writeStored(value)
  }, [])
  return { view, setView }
}
