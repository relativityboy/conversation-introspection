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
import { useSearchParams } from 'react-router-dom'
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

// Client mirror of the server's `_has_resolved_dispatch()` (routes/sessions.py, final review
// C1): a dispatch tool_use block carries no prose of its own (production: 445 dispatch rows, 0
// with any text), so `proseVisible` alone would hide the row and strand the SubagentChip outside
// `all`. True when some block in the row is a `tool_use` whose id is in the caller-supplied set
// of tool_use_ids that resolve to a CAPTURED subagent transcript (`useDispatchToolUseIds`,
// transcripts-context.ts).
function hasResolvedDispatch(message: MessageOut, dispatchToolUseIds: ReadonlySet<string>): boolean {
  return message.blocks.some(
    (b) =>
      b.block_kind === 'tool_use' && b.tool_use_id != null && dispatchToolUseIds.has(b.tool_use_id),
  )
}

/**
 * Client mirror of the server's `_view_filter` (`server/src/introspect/api/routes/sessions.py`,
 * using `CHAT_KINDS` from `server/src/introspect/schema/authorship.py`) — the SAME predicate, so
 * the rows the reader shows/hides and the rows the raw inspector's prev/next skip can never drift.
 * NULL-tolerant: a message not yet backfilled with an `authorship_kind` (migrate→reparse deploy
 * window) degrades to the legacy type+content rule rather than vanishing from every filtered view.
 *
 * `dispatchToolUseIds` (default: empty) is the resolved-dispatch tool_use id set — see
 * `useDispatchToolUseIds` (transcripts-context.ts). A caller that can't reach the transcripts
 * context (a bare unit-test render outside any `TranscriptsProvider`) safely omits it: every
 * tool_use degrades to "unresolved", exactly matching the render-time behavior of `SubagentChip`
 * under the same conditions.
 */
export function isVisibleInView(
  message: MessageOut,
  view: ViewMode,
  dispatchToolUseIds: ReadonlySet<string> = new Set(),
): boolean {
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

  return kindOk && (proseVisible(message) || hasResolvedDispatch(message, dispatchToolUseIds))
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

// --- category filter (Task T10) -----------------------------------------------------------
//
// The five-checkbox replacement for the three-state ViewMode toggle ABOVE. `ViewMode`/
// `useViewMode`/`isVisibleInView`/`ViewToggle` are deliberately left untouched — they remain the
// mechanism for RawRecordInspector's own independent in-modal filter (a narrower, separate
// concern from the reader header this task replaces; see the T10 write-up). Everything below is
// NEW and additive.
//
// Slugs are FROZEN (shared contract with the server's T9 task — see
// claude_notes/2026-09-20-15-58-plan-task-queue-round-one.md "Feature 3"): every (message, block)
// maps to exactly one category, never dropped silently.
export type CategorySlug =
  | 'you-chat'
  | 'claude-chat'
  | 'claude-thinking'
  | 'tool-traffic'
  | 'harness-system'

export const ALL_CATEGORIES: readonly CategorySlug[] = [
  'you-chat',
  'claude-chat',
  'claude-thinking',
  'tool-traffic',
  'harness-system',
]

export const ALL_CATEGORIES_SET: ReadonlySet<CategorySlug> = new Set(ALL_CATEGORIES)

export const CATEGORY_LABELS: Record<CategorySlug, string> = {
  'you-chat': 'You — chat',
  'claude-chat': 'Claude — chat',
  'claude-thinking': 'Claude — thinking',
  'tool-traffic': 'Tool traffic',
  'harness-system': 'Harness/system',
}

// Kind buckets shared with MessageTurn's eyebrow logic — moved here so `categoryOfBlock` and
// `voiceClassOf`/`accentFor` (MessageTurn.tsx) can never drift on who counts as "human" vs
// "Claude" voiced. MessageTurn imports these back rather than keeping its own copy.
export const HUMAN_KINDS: ReadonlySet<string> = new Set([
  'human_typed',
  'human_queued',
  'human_inferred',
])
export const CLAUDE_KINDS: ReadonlySet<string> = new Set(['claude', 'dispatch', 'coordinator'])

// The category contract's "human family" (server sessions.py `_YOU_CHAT_AUTHORSHIP_KINDS`) —
// HUMAN_KINDS above PLUS `attachment_queued_human` and `interrupt_marker`. A separate constant
// from HUMAN_KINDS on purpose: HUMAN_KINDS drives the eyebrow's narrower dawn-accent grouping
// (§3.3, MessageTurn.tsx), which special-cases attachment_queued_human on its own for the
// fourth "attachment" voice and doesn't include interrupt_marker (that kind gets the plain
// SYSTEM (INTERRUPT) label/mist accent there) — two independent groupings over the same kind
// vocabulary, for two independent purposes.
const YOU_CHAT_AUTHORSHIP_KINDS: ReadonlySet<string> = new Set([
  ...HUMAN_KINDS,
  'attachment_queued_human',
  'interrupt_marker',
])

// Owner-ratified preset↔set equivalence, corrected 2026-09-22 against the server's T9
// implementation (server/src/introspect/api/routes/sessions.py `_categorize` + its own
// equivalence test suite, `test_select_*_equals_view_*` in test_api_sessions.py) — the FROZEN
// contract this file's earlier draft got wrong before that code existed to check against.
// `select=` prunes BLOCKS within an already-visible row (`view=` never does), so reproducing
// `view=chat`'s exact rows AND blocks needs `claude-thinking` in the `chat` set too: a claude
// turn combining `thinking` + `text` shows BOTH blocks under `view=chat` today (thinking was
// never block-level gated by the retired ViewMode — see `isVisibleInView` above), and dropping
// `claude-thinking` from the equivalent `select=` set would silently prune that block. Same
// reasoning extends `chat-harness` by one slug. "EXISTING VIEW BEHAVIOR WINS" (server test
// docstring) is the tiebreak both sides independently landed on.
export const PRESET_SETS: Record<ViewMode, ReadonlySet<CategorySlug>> = {
  chat: new Set(['you-chat', 'claude-chat', 'claude-thinking']),
  'chat-harness': new Set(['you-chat', 'claude-chat', 'claude-thinking', 'harness-system']),
  all: ALL_CATEGORIES_SET,
}

function setsEqual<T>(a: ReadonlySet<T>, b: ReadonlySet<T>): boolean {
  if (a.size !== b.size) return false
  for (const value of a) if (!b.has(value)) return false
  return true
}

/** The preset name whose set exactly equals `selection`, or `null` for a custom combination.
 * Drives BOTH the URL's `view=`-vs-`select=` choice (urlState.ts) and the CategoryFilter chips'
 * highlight state — the SAME equality check for both, so a chip can never read "active" while the
 * URL disagrees. */
export function presetForSelection(selection: ReadonlySet<CategorySlug>): ViewMode | null {
  for (const [preset, set] of Object.entries(PRESET_SETS) as Array<[ViewMode, ReadonlySet<CategorySlug>]>) {
    if (setsEqual(selection, set)) return preset
  }
  return null
}

/** The FROZEN per-block category contract, a byte-for-byte port of the server's `_categorize`
 * (sessions.py, Task T9; resolved-dispatch routing added Task T12) — total over every
 * (authorship_kind, block_kind) pair, never raises, never returns outside the five slugs.
 * First-match priority, mirrored exactly:
 *
 *  1. every block of a `tool_result`-AUTHORSHIP message is `tool-traffic` — "the exchange is a
 *     unit", so the message-level override beats whatever an individual block looks like.
 *  2. a `tool_use` BLOCK whose id is in `dispatchToolUseIds` (a captured subagent transcript's
 *     `parent_tool_use_id` — the SAME join `SubagentChip`/`useDispatchToolUseIds` use to decide
 *     a chip resolves) is `claude-chat` — owner ruling 2026-09-23: a resolved dispatch is a
 *     doorway into a Claude-voiced conversation, not mechanical traffic (supersedes this
 *     function's earlier "deliberately no carve-out" contract, which the server's own
 *     `test_select_does_not_reproduce_resolved_dispatch_chip_visibility` test used to pin —
 *     that server test is now flipped to
 *     `test_select_reproduces_resolved_dispatch_chip_visibility_as_claude_chat`). Any OTHER
 *     `tool_use` BLOCK (unresolved, or `dispatchToolUseIds` omitted/empty) is `tool-traffic`
 *     regardless of its message's authorship ("wherever it appears") — checked before the
 *     family branches so a tool_use on a claude/dispatch/coordinator message routes here, not
 *     to claude-chat/claude-thinking, unless the resolved case above already claimed it.
 *  3. the human family (`YOU_CHAT_AUTHORSHIP_KINDS`) → `you-chat`.
 *  4. the claude family (`CLAUDE_KINDS`): a `thinking` block → `claude-thinking`, everything
 *     else → `claude-chat`.
 *  5. everything else — system records, skill/command furniture, notifications, non-rescued
 *     attachments, unclassified/NULL authorship (no legacy type-based tolerance here, unlike
 *     `isVisibleInView`'s `legacyFallback` — the server's `_categorize` floors a NULL kind to
 *     harness-system unconditionally, unbacked by `message.type`), and any block kind this
 *     client predates — → `harness-system`, the exhaustive floor. Never hidden by omission.
 *
 * `dispatchToolUseIds` defaults to empty for callers outside a `TranscriptsProvider` (bare unit
 * renders): every `tool_use` then degrades to `tool-traffic`, matching `SubagentChip`'s own
 * degrade-to-ToolBlock behavior under the same conditions — see `useDispatchToolUseIds`
 * (transcripts-context.ts).
 */
export function categoryOfBlock(
  block: BlockOut,
  message: MessageOut,
  dispatchToolUseIds: ReadonlySet<string> = new Set(),
): CategorySlug {
  if (message.authorship_kind === 'tool_result') return 'tool-traffic'
  if (block.block_kind === 'tool_use') {
    return block.tool_use_id != null && dispatchToolUseIds.has(block.tool_use_id)
      ? 'claude-chat'
      : 'tool-traffic'
  }
  const kind = message.authorship_kind
  if (kind != null && YOU_CHAT_AUTHORSHIP_KINDS.has(kind)) return 'you-chat'
  if (kind != null && CLAUDE_KINDS.has(kind)) {
    return block.block_kind === 'thinking' ? 'claude-thinking' : 'claude-chat'
  }
  return 'harness-system'
}

/** Whether `block` renders under `selection` — the block-level half of the FROZEN contract ("a
 * block renders iff its category is selected"). Used by MessageTurn's `Block` dispatcher. */
export function isBlockCategorySelected(
  block: BlockOut,
  message: MessageOut,
  selection: ReadonlySet<CategorySlug>,
  dispatchToolUseIds: ReadonlySet<string> = new Set(),
): boolean {
  return selection.has(categoryOfBlock(block, message, dispatchToolUseIds))
}

/** Row-level half of the FROZEN contract ("a row disappears iff none of its blocks are
 * selected") — the selection-aware sibling of `isVisibleInView` above, used the SAME places:
 * MessageTurn's row gate and (were it ever threaded there) RawRecordInspector-style prev/next
 * navigation.
 *
 * A message with ZERO blocks has no block whose category could ever be selected — the server
 * contract update (T17 follow-up, 2026-09-25) covers this with a MESSAGE-level rule instead of a
 * block-level one: such a message categorizes as `harness-system`, visible iff `harness-system`
 * is in `selection`. This restores "select=<all five> ≡ old view=all" including blockless
 * furniture (bare `system`-type records, non-rescued zero-block `attachment` stubs) — the earlier
 * T10/T12-era contract left these ALWAYS invisible (even under all-five), a deliberate divergence
 * from the retired `all` view that this update reverses; see
 * claude_notes/2026-09-25-sdd-filter-dropdown-writeups.md's T17 follow-up section for the change
 * record.
 *
 * One refinement beyond a bare `.some(categoryOfBlock ∈ selection)`, ported from the server's
 * `_block_matches_categories` (found via genuine red on their side, claude_notes/2026-09-22-sdd-
 * checkboxes-writeups.md "T9" nuance 4): an EMPTY `text` block never counts toward row
 * visibility, even though `categoryOfBlock` still slots it into its message's family. The CLI's
 * real production shape for a dispatch tool_use is an empty companion `text` block riding along
 * — without this guard, that empty block's category (its message's voice, e.g. `claude-chat`)
 * alone would make the row read "visible" under a selection that excludes `tool-traffic`, even
 * though nothing actually renders (`Block()` already refuses to render empty text, and the
 * tool_use block is separately gated out) — an empty ghost row instead of a correctly-vanished
 * one. Scoped ONLY to this row-visibility check, mirroring the server's own scoping: an empty
 * text block that rides along on an otherwise-visible row (visible via some OTHER real block)
 * still passes `isBlockCategorySelected`/`Block()`'s per-block gate unchanged — it just renders
 * nothing there too, exactly like today.
 *
 * `dispatchToolUseIds` (Task T12, default empty) threads straight through to `categoryOfBlock`
 * so a resolved dispatch row's sole `tool_use` block (owner ruling 2026-09-23: claude-chat, not
 * tool-traffic) keeps this row visible under a selection that includes `claude-chat`, matching
 * exactly what `Block()`/`SubagentChip` render for the same row — see `categoryOfBlock`'s doc. */
export function isVisibleInSelection(
  message: MessageOut,
  selection: ReadonlySet<CategorySlug>,
  dispatchToolUseIds: ReadonlySet<string> = new Set(),
): boolean {
  if (message.blocks.length === 0) return selection.has('harness-system')
  return message.blocks.some((block) => {
    if (!selection.has(categoryOfBlock(block, message, dispatchToolUseIds))) return false
    if (block.block_kind === 'text' && (block.text_content == null || block.text_content === '')) {
      return false
    }
    return true
  })
}

// --- URL persistence + hook (Task T10; `?view=` retired Task T17) --------------------------
//
// `?select=<csv>` carries the reader header's checkbox selection — the ONLY wire param (owner
// ruling 2026-09-25): the earlier `?view=<preset>` pretty-URL shorthand is deleted entirely, both
// read and write, matching the server's own frozen contract (`view=` deleted server-side too). A
// legacy `?view=` arriving in a URL is simply ignored, never consulted — it opens at the chat
// default exactly as if no param were present at all, not silently upgraded to the preset it used
// to name. `?select=` absent, empty, or made of only unrecognized slugs likewise falls back to
// the `chat` preset. Co-located here (rather than urlState.ts, which the rest of the app's filter
// state lives in) because `readSelection`/`writeSelection` need
// `PRESET_SETS`/`presetForSelection`/`CategorySlug` above, and urlState.ts is deliberately
// framework-free/domain-free — putting them there would either duplicate this module's category
// logic or create an import cycle.
const CATEGORY_SLUGS: ReadonlySet<CategorySlug> = new Set(ALL_CATEGORIES)

function isCategorySlug(value: string): value is CategorySlug {
  return CATEGORY_SLUGS.has(value as CategorySlug)
}

/** Reads the reader's category selection from `?select=` only. Absent/unrecognized (no param, or
 * a `select=` that parses to nothing usable) falls back to the `chat` preset — the same default
 * the retired `useViewMode` used. A `?view=` anywhere in the URL is never read here — see the
 * section doc above. */
export function readSelection(searchParams: URLSearchParams): ReadonlySet<CategorySlug> {
  const selectRaw = searchParams.get('select')
  if (selectRaw !== null) {
    const slugs = selectRaw
      .split(',')
      .map((slug) => slug.trim())
      .filter(isCategorySlug)
    if (slugs.length > 0) return new Set(slugs)
  }
  return PRESET_SETS.chat
}

/**
 * Returns a NEW `URLSearchParams` with the category selection written — every other param passes
 * through untouched, and `prev` is never mutated. When `selection` equals the `chat` DEFAULT
 * exactly, deletes `?select=` entirely (clean URLs for the common case); otherwise writes the CSV
 * `?select=` — including for the `chat-harness`/`all` presets, which no longer get a pretty
 * `?view=` shorthand (that param is dead code on the write side, deleted along with the read side
 * above).
 */
export function writeSelection(
  prev: URLSearchParams,
  selection: ReadonlySet<CategorySlug>,
): URLSearchParams {
  const next = new URLSearchParams(prev)
  if (presetForSelection(selection) === 'chat') {
    next.delete('select')
  } else {
    next.set('select', [...selection].join(','))
  }
  return next
}

/**
 * URL-backed replacement for the retired `useViewMode` (Task T10). Same "ONE owner per reader
 * page" contract as the docstring at the top of this file describes — each `SessionPage` /
 * `SubagentPage` calls this exactly once and threads `{selection, setSelection}` down to both the
 * header's CategoryFilter and the ConversationView body, so they can never desync (plan critique
 * F4, carried over). Deliberately URL-only (no localStorage fallback): the retired hook's
 * `introspect.view.v1` stickiness is retired along with it (zero-legacy) — a shared/bookmarked
 * link is the shareable state now, not a per-browser default. `setSelection` pushes a new history
 * entry (matches the sidebar's `writeProjects`/`writeSidebarParams` callers, e.g. Sidebar's
 * favorites chip) rather than replacing, so checkbox changes are back/forward-navigable.
 */
export function useCategorySelection(): {
  selection: ReadonlySet<CategorySlug>
  setSelection: (selection: ReadonlySet<CategorySlug>) => void
} {
  const [searchParams, setSearchParams] = useSearchParams()
  const selection = readSelection(searchParams)
  const setSelection = useCallback(
    (next: ReadonlySet<CategorySlug>) => {
      setSearchParams((prev) => writeSelection(prev, next))
    },
    [setSearchParams],
  )
  return { selection, setSelection }
}
