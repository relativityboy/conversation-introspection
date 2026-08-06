import type { CSSProperties, ReactNode } from 'react'
import { useCallback, useRef, useState } from 'react'
import { Virtuoso } from 'react-virtuoso'
import { ApiError, fetchMessages } from '../../api/client'
import { useMessages } from '../../api/hooks'
import type { MessageList, MessageOut } from '../../api/types'
import { applyGlow } from '../../lib/glow'
import { MessageTurn } from './MessageTurn'
import { RawRecordInspector } from './RawRecordInspector'

const PAGE_SIZE = 100

// A text button that reads as an inline link inside the calm notice — no chrome, dragonfly ink.
const LINK_BUTTON_STYLE: CSSProperties = {
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  fontFamily: 'inherit',
  fontSize: 'inherit',
  color: 'var(--dragonfly)',
}

export interface ConversationViewProps {
  transcriptId: number
  /** Seed the window around this record and start scrolled to it. Accepted now so the
   * windowing model is complete; the route plumbing that supplies it lands in Task 7. */
  initialAroundUuid?: string
  /** Conversation-only mode, OWNED by the page (useChatOnly) and passed in — never sourced
   * locally, so header and body can't desync (plan critique F4). Threads through all three fetch
   * sites and down to MessageTurn's block-level hiding. */
  chatOnly: boolean
  /** Passed only so the around-404 notice can offer "show all message types" (critique #12). */
  setChatOnly: (value: boolean) => void
}

// chat_only must ride EVERY fetch site (seed + both edge loaders): an unfiltered edge page spliced
// into a filtered window would corrupt the offset math (§14.4). Absent (not `false`) when off, so
// the opts object — and therefore the react-query key — stays identical to the pre-toggle shape.
function withChatOnly<T extends object>(opts: T, chatOnly: boolean): T & { chat_only?: true } {
  return chatOnly ? { ...opts, chat_only: true } : opts
}

// NOTE(claude): fetch strategy — the INITIAL page goes through the useMessages react-query
// hook (declarative pending/error states, cached across route revisits); the edge pages
// fetched by startReached/endReached call fetchMessages directly. Deliberate split: edge
// pages accumulate imperatively into one window (a prepend must atomically decrement
// firstItemIndex with the same state update), and caching each scroll-driven page under its
// own query key would buy nothing while complicating that atomicity.
export function ConversationView({
  transcriptId,
  initialAroundUuid,
  chatOnly,
  setChatOnly,
}: ConversationViewProps) {
  // Re-seed control. `seedOverride` pins an explicit page — Top → offset 0, End → the last page,
  // and the 404 "view from the beginning" recovery → offset 0 — winning over the deep-link
  // `initialAroundUuid` WITHOUT a route change. `seedNonce` bumps on every press so the window
  // remounts and re-seeds even when the target page is unchanged (e.g. Top while already at the
  // top). Adjust-state-during-render (see ConversationSearch) clears the override the moment a
  // fresh deep link arrives, so a later valid /m/ target still seeds correctly.
  const [seedOverride, setSeedOverride] = useState<SeedOverride | null>(null)
  const [seedNonce, setSeedNonce] = useState(0)
  const [seenAround, setSeenAround] = useState(initialAroundUuid)
  if (initialAroundUuid !== seenAround) {
    setSeenAround(initialAroundUuid)
    setSeedOverride(null)
  }
  const reseed = (override: SeedOverride) => {
    setSeedOverride(override)
    setSeedNonce((n) => n + 1)
  }
  const around = seedOverride ? undefined : initialAroundUuid

  const initial = useMessages(
    transcriptId,
    withChatOnly(
      seedOverride
        ? { offset: seedOverride.offset, limit: PAGE_SIZE }
        : around
          ? { around, limit: PAGE_SIZE }
          : { offset: 0, limit: PAGE_SIZE },
      chatOnly,
    ),
  )

  if (initial.isPending) return <Calm>…</Calm>
  if (initial.isError) {
    // A 404 on the around-fetch means the target message isn't in THIS transcript — a
    // not-found, not the archive being offline. Offer a calm jump to the start instead (and
    // useMessages' retry policy skips the react-query retry storm for these 404s).
    if (around && initial.error instanceof ApiError && initial.error.status === 404) {
      // Under chat_only the 404 may mean "the target is a tool/system record filtered OUT of this
      // set", not "not in this transcript at all" — so offer a second recovery that KEEPS the same
      // around seed and just drops the filter (critique #12), distinct from "view from the
      // beginning" which drops the around seed. Shown only when chatOnly (meaningless otherwise).
      return (
        <Calm>
          message not found in this conversation{' '}
          <button
            type="button"
            onClick={() => reseed({ offset: 0, landAtEnd: false })}
            style={LINK_BUTTON_STYLE}
          >
            view from the beginning
          </button>
          {chatOnly && (
            <>
              {' · '}
              <button
                type="button"
                onClick={() => setChatOnly(false)}
                style={LINK_BUTTON_STYLE}
              >
                show all message types
              </button>
            </>
          )}
        </Calm>
      )
    }
    return <Calm>archive offline</Calm>
  }
  if (initial.data.total === 0) return <Calm>Nothing recorded in this transcript.</Calm>

  // The key resets the window state whenever the identity of the stream changes — a new
  // transcript or a new around-target must re-seed rather than mutate the old window. Keying on
  // the EFFECTIVE `around` also re-seeds cleanly when a Top/End/"view from the beginning" drops
  // it, on `chatOnly` so toggling remounts the window (an unfiltered edge page mixed into a
  // filtered window would corrupt the offset math, §14.4), and on `seedNonce` so a repeated Top/
  // End press re-seeds even when the target page is unchanged. The remount is also the isolation
  // boundary for an in-flight edge fetch — see MessageStream's pendingRef note.
  const total = initial.data.total
  return (
    <div style={{ position: 'relative', height: '100%' }}>
      <MessageStream
        key={`${transcriptId}:${around ?? ''}:${chatOnly ? 1 : 0}:${seedNonce}`}
        transcriptId={transcriptId}
        seed={initial.data}
        initialAroundUuid={around}
        chatOnly={chatOnly}
        landAtEnd={seedOverride?.landAtEnd ?? false}
      />
      <ReaderJumpControls
        onTop={() => reseed({ offset: 0, landAtEnd: false })}
        // End pins the LAST page within the CURRENT chat_only filter — `total` already respects
        // it (server-side), so the same arithmetic works filtered or not.
        onEnd={() => reseed({ offset: Math.max(0, total - PAGE_SIZE), landAtEnd: true })}
      />
    </div>
  )
}

// The re-seed target: an explicit page offset plus whether to land scrolled to the bottom (End)
// rather than the top (Top / recovery).
interface SeedOverride {
  offset: number
  landAtEnd: boolean
}

const CONTROLS_WRAP_STYLE: CSSProperties = {
  position: 'absolute',
  bottom: 14,
  right: 16,
  display: 'flex',
  gap: 6,
  // Above the virtualized rows so the pills stay clickable while content scrolls beneath them.
  zIndex: 2,
}

const CONTROL_BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  color: 'var(--mist)',
  background: 'var(--shore)',
  border: '1px solid transparent',
  borderRadius: 999,
  padding: '4px 10px',
  cursor: 'pointer',
}

// NOTE(claude): floated at the bottom-right CORNER of the scroller. The raw-inspector `{}`
// affordances sit at each turn's TOP-right, so a bottom-corner group never collides with the
// topmost (always-visible) row's `{}`. Two quiet Still-Water pills — re-seed to offset 0 (top)
// or the last page (end); the window remount does the scroll, so these hold no scroll state.
function ReaderJumpControls({ onTop, onEnd }: { onTop: () => void; onEnd: () => void }) {
  return (
    <div style={CONTROLS_WRAP_STYLE}>
      <button type="button" aria-label="top" onClick={onTop} style={CONTROL_BUTTON_STYLE}>
        ↑ top
      </button>
      <button type="button" aria-label="end" onClick={onEnd} style={CONTROL_BUTTON_STYLE}>
        ↓ end
      </button>
    </div>
  )
}

// NOTE(claude): bidirectional windowing model (plan Task 5, binding). The loaded window is
// `{firstItemIndex, items}`: `items` covers the absolute message range
// [firstItemIndex, firstItemIndex + items.length), so the mapping is
//   absolute = firstItemIndex + arrayIndex
// and Virtuoso's itemContent (which receives ABSOLUTE indexes once firstItemIndex is set)
// looks up items[absolute - firstItemIndex]. Decrementing firstItemIndex on prepend is what
// keeps the scroll position stable — virtuoso anchors by absolute index.
interface StreamWindow {
  firstItemIndex: number
  items: MessageOut[]
  total: number
}

interface MessageStreamProps {
  transcriptId: number
  seed: MessageList
  initialAroundUuid?: string
  chatOnly: boolean
  /** End re-seed: start scrolled to the LAST message of the seeded (last) page. */
  landAtEnd: boolean
}

function MessageStream({
  transcriptId,
  seed,
  initialAroundUuid,
  chatOnly,
  landAtEnd,
}: MessageStreamProps) {
  const [stream, setStream] = useState<StreamWindow>(() => ({
    firstItemIndex: seed.offset,
    items: seed.items,
    total: seed.total,
  }))
  // One edge fetch at a time — virtuoso can fire startReached/endReached repeatedly while a
  // page is still in flight; without the guard the same page would prepend/append twice.
  //
  // pendingRef is per-INSTANCE (a fresh useRef per MessageStream), and so is `stream`. Toggling
  // chatOnly changes ConversationView's key, unmounting THIS instance and mounting a new one: any
  // edge fetch still in flight here resolves into a setStream on the unmounted instance (a React
  // no-op) — it can never splice a stale unfiltered page into the new filtered window. The new
  // instance starts with pendingRef=false and re-seeds from the fresh (filtered) `seed`.
  const pendingRef = useRef(false)

  // The raw-record inspector (§15.2) is a single reader-level instance, NOT one-per-row: a row's
  // `{}` sets the current record uuid here, the modal reads `stream.items` as its traversal order,
  // and closing clears it. Hosted here (not in ConversationView) because this is where the loaded
  // window lives — navigation stays inside `stream.items`, never re-fetching from the modal.
  const [inspectUuid, setInspectUuid] = useState<string | null>(null)

  // Array index (within the seeded page) of the around-target, or of the last loaded message for
  // `landAtEnd`; undefined when there's no real target (bare top-of-window landing).
  //
  // NOTE(claude): ARRAY-LOCAL, not absolute — walk fix 9b round 4, 2026-08-05. A round-4 fiber
  // inspection suggested this should be `seed.offset + arrayIndex` (absolute, matching
  // itemContent's space) instead, reasoning virtuoso resolves this prop against the same
  // firstItemIndex-shifted tree it uses everywhere else. That direction was tried and PROVEN
  // WRONG by two independent checks: (1) source — `listStateSystem.ts`'s
  // `buildListStateFromItemCount` resolves the initial index via a direct `data[index +
  // initialTopMostItemIndexNumber]` lookup into the LOADED array, and only the *output* items get
  // `+firstItemIndex` applied afterward (in `transposeItems`) to produce the absolute index
  // `itemContent` receives — the shift is a presentation-layer step, not the space this prop
  // lives in. (2) live A/B test against a real nonzero-offset deeplink (offset 300, target array
  // index 50): the array-local index (50) landed on the target instantly, one fetch; the
  // "absolute" index (350) never found it and instead drove `endReached` into a runaway fetch
  // loop (400, 500, 600…), because 350 is nonsense relative to the ~100 LOCAL items actually
  // loaded. This is exactly this reader's existing convention — see `itemContent` below, which
  // separately converts absolute-to-local via `stream.items[absoluteIndex - stream.firstItemIndex]`
  // for its OWN (correct, unrelated) reason.
  const targetIndex = (() => {
    if (landAtEnd) return seed.items.length > 0 ? seed.items.length - 1 : undefined
    if (!initialAroundUuid) return undefined
    const index = seed.items.findIndex((m) => m.record_uuid === initialAroundUuid)
    return index === -1 ? undefined : index
  })()
  // The target lands CENTERED (2026-07-20 walk finding — top-edge landing hid the context above
  // the highlighted message and read as "not what I hoped for"), via the OBJECT `{index, align}`
  // form of `initialTopMostItemIndex` — no target (bare top-of-window landing) stays the plain
  // number 0, which needs no alignment.
  //
  // NOTE(claude): walk fix 9b round 3 replaced this object form with a plain number, diagnosing a
  // main-thread livelock in virtuoso's initial-align resolution on at least one profile-dependent
  // Chrome configuration and adding a one-shot post-mount `scrollToIndex` ref call to re-apply the
  // alignment imperatively instead. That livelock evidence was retracted — it traced to an
  // automation-tab artifact, not a real defect reachable from ordinary browser use. The one-shot
  // effect is removed here because it never actually controlled the landing anyway: virtuoso
  // itself re-issues `scrollToIndex` with the CURRENT `initialTopMostItemIndex` value after
  // measurement settles, 4 rAFs later (`node_modules/react-virtuoso/dist/index.mjs:1178-1216`,
  // the `je(4, …)` call at :1211, `je` defined :1164) — so a plain number (align undefined →
  // 'start') always won the race and overwrote our mount-effect's centered/end-aligned call,
  // which is why the landing regressed to the top edge. Passing the object form here lets
  // virtuoso's own post-measurement correction land at the right alignment instead of fighting it.
  const [initialTopMostItemIndex] = useState<number | { index: number; align: 'center' | 'end' }>(
    () =>
      targetIndex === undefined
        ? 0
        : { index: targetIndex, align: landAtEnd ? 'end' : 'center' },
  )

  // Deep-link arrival glow. A ref callback (not a post-render querySelector) fires exactly when
  // the target row's element mounts — robust against virtuoso's async measure-then-render and
  // the row scrolling in/out of the virtual window. The once-guard makes glow fire a single time
  // (and passing the STABLE callback keeps React from re-invoking it on every re-render).
  const glowedRef = useRef(false)
  const glowTarget = useCallback((el: HTMLElement | null) => {
    if (el && !glowedRef.current) {
      glowedRef.current = true
      applyGlow(el)
    }
  }, [])

  const loadBefore = useCallback(async () => {
    const { firstItemIndex } = stream
    if (pendingRef.current || firstItemIndex === 0) return
    pendingRef.current = true
    try {
      const offset = Math.max(0, firstItemIndex - PAGE_SIZE)
      // limit is the exact gap (≤ PAGE_SIZE), not a flat 100: an around-seeded
      // firstItemIndex is rarely page-aligned, and over-fetching past the window's start
      // would prepend duplicates.
      const page = await fetchMessages(
        transcriptId,
        withChatOnly({ offset, limit: firstItemIndex - offset }, chatOnly),
      )
      setStream((prev) => ({
        firstItemIndex: prev.firstItemIndex - page.items.length,
        items: [...page.items, ...prev.items],
        total: page.total,
      }))
    } catch {
      // Calm failure: leave the window untouched; scrolling up again retries.
    } finally {
      pendingRef.current = false
    }
  }, [stream, transcriptId, chatOnly])

  const loadAfter = useCallback(async () => {
    const offset = stream.firstItemIndex + stream.items.length
    if (pendingRef.current || offset >= stream.total) return
    pendingRef.current = true
    try {
      const page = await fetchMessages(
        transcriptId,
        withChatOnly({ offset, limit: PAGE_SIZE }, chatOnly),
      )
      setStream((prev) => ({
        ...prev,
        items: [...prev.items, ...page.items],
        total: page.total,
      }))
    } catch {
      // Calm failure: leave the window untouched; scrolling down again retries.
    } finally {
      pendingRef.current = false
    }
  }, [stream, transcriptId, chatOnly])

  // NOTE(claude): totalCount is the WINDOW length, not the archive total — deliberate
  // deviation from the plan's literal "totalCount=response.total". With firstItemIndex set,
  // virtuoso renders absolute indexes [firstItemIndex, firstItemIndex + totalCount); passing
  // the archive total would declare unloaded ghost rows from the window's end all the way to
  // the archive's end, and endReached would fire only past the LAST ghost row — never
  // appending. The archive total lives in stream.total and is what stops loadAfter at the
  // boundary.
  return (
    <>
      <Virtuoso
        style={{ height: '100%' }}
        totalCount={stream.items.length}
        firstItemIndex={stream.firstItemIndex}
        initialTopMostItemIndex={initialTopMostItemIndex}
        // NOTE(claude): 1200px of pre-rendered runway above the viewport (2026-07-20 walk
        // finding): startReached then fires while the join is still off-screen, so the
        // prepend's scrollTop correction can't fight an in-flight user scroll — that fight
        // was the scrollbar-teleport + stutter relativityboy felt when scrolling up from a
        // deep-linked message. Bottom kept modest; appends don't correct scrollTop.
        // NOTE(claude): 1200 measured better than 2400 (2026-07-20 Playwright runs: 1 vs 3
        // scroll-corrections per 12-step sweep) — a larger runway triggers MORE prepends per
        // distance during sustained scrolling. Occasional scrollbar re-anchoring at page joins
        // is inherent windowed-list physics; don't chase zero by inflating this number.
        increaseViewportBy={{ top: 1200, bottom: 400 }}
        startReached={loadBefore}
        endReached={loadAfter}
        itemContent={(absoluteIndex) => {
          const message = stream.items[absoluteIndex - stream.firstItemIndex]
          if (!message) return null
          const isTarget = message.record_uuid === initialAroundUuid
          return (
            <div
              data-record-uuid={message.record_uuid}
              // The deep-link target keeps a persistent marker (dawn accent + faint wash) after
              // the transient glow fades (§9 amendment 2026-07-20). `isTarget` is false once
              // "view from the beginning" drops the around seed, so recovery clears the marker.
              className={isTarget ? 'deep-link-target' : undefined}
              ref={isTarget ? glowTarget : undefined}
              style={{ padding: '0 24px' }}
            >
              <MessageTurn message={message} chatOnly={chatOnly} onInspect={setInspectUuid} />
            </div>
          )
        }}
      />
      {inspectUuid !== null && (
        <RawRecordInspector
          items={stream.items}
          initialUuid={inspectUuid}
          parentChatOnly={chatOnly}
          onClose={() => setInspectUuid(null)}
        />
      )}
    </>
  )
}

function Calm({ children }: { children: ReactNode }) {
  return <p style={{ color: 'var(--mist)', fontSize: 13, padding: '10px 24px' }}>{children}</p>
}
