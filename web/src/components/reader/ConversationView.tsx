import type { CSSProperties, ReactNode } from 'react'
import { useCallback, useRef, useState } from 'react'
import { Virtuoso } from 'react-virtuoso'
import { ApiError, fetchMessages } from '../../api/client'
import { useMessages } from '../../api/hooks'
import type { MessageList, MessageOut } from '../../api/types'
import { applyGlow } from '../../lib/glow'
import { MessageTurn } from './MessageTurn'

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
}

// NOTE(claude): fetch strategy — the INITIAL page goes through the useMessages react-query
// hook (declarative pending/error states, cached across route revisits); the edge pages
// fetched by startReached/endReached call fetchMessages directly. Deliberate split: edge
// pages accumulate imperatively into one window (a prepend must atomically decrement
// firstItemIndex with the same state update), and caching each scroll-driven page under its
// own query key would buy nothing while complicating that atomicity.
export function ConversationView({ transcriptId, initialAroundUuid }: ConversationViewProps) {
  // "View from the beginning" recovery. When the around-target isn't in this transcript (a 404
  // — e.g. a stale deep link), the reader drops the around-seed and re-fetches offset 0 WITHOUT
  // a route change. Adjust-state-during-render (see ConversationSearch) clears the override the
  // moment a fresh deep link arrives, so a later valid /m/ target still seeds correctly.
  const [fromBeginning, setFromBeginning] = useState(false)
  const [seenAround, setSeenAround] = useState(initialAroundUuid)
  if (initialAroundUuid !== seenAround) {
    setSeenAround(initialAroundUuid)
    setFromBeginning(false)
  }
  const around = fromBeginning ? undefined : initialAroundUuid

  const initial = useMessages(
    transcriptId,
    around ? { around, limit: PAGE_SIZE } : { offset: 0, limit: PAGE_SIZE },
  )

  if (initial.isPending) return <Calm>…</Calm>
  if (initial.isError) {
    // A 404 on the around-fetch means the target message isn't in THIS transcript — a
    // not-found, not the archive being offline. Offer a calm jump to the start instead (and
    // useMessages' retry policy skips the react-query retry storm for these 404s).
    if (around && initial.error instanceof ApiError && initial.error.status === 404) {
      return (
        <Calm>
          message not found in this conversation{' '}
          <button type="button" onClick={() => setFromBeginning(true)} style={LINK_BUTTON_STYLE}>
            view from the beginning
          </button>
        </Calm>
      )
    }
    return <Calm>archive offline</Calm>
  }
  if (initial.data.total === 0) return <Calm>Nothing recorded in this transcript.</Calm>

  // The key resets the window state whenever the identity of the stream changes — a new
  // transcript or a new around-target must re-seed rather than mutate the old window. Keying on
  // the EFFECTIVE `around` also re-seeds cleanly when "view from the beginning" drops it.
  return (
    <MessageStream
      key={`${transcriptId}:${around ?? ''}`}
      transcriptId={transcriptId}
      seed={initial.data}
      initialAroundUuid={around}
    />
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
}

function MessageStream({ transcriptId, seed, initialAroundUuid }: MessageStreamProps) {
  const [stream, setStream] = useState<StreamWindow>(() => ({
    firstItemIndex: seed.offset,
    items: seed.items,
    total: seed.total,
  }))
  // One edge fetch at a time — virtuoso can fire startReached/endReached repeatedly while a
  // page is still in flight; without the guard the same page would prepend/append twice.
  const pendingRef = useRef(false)

  // Array index (virtuoso interprets initialTopMostItemIndex in list space, not absolute
  // space) of the around-target within the seeded page; top of the stream when absent.
  const [initialTopMostItemIndex] = useState(() => {
    if (!initialAroundUuid) return 0
    const index = seed.items.findIndex((m) => m.record_uuid === initialAroundUuid)
    return index === -1 ? 0 : index
  })

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
      const page = await fetchMessages(transcriptId, {
        offset,
        limit: firstItemIndex - offset,
      })
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
  }, [stream, transcriptId])

  const loadAfter = useCallback(async () => {
    const offset = stream.firstItemIndex + stream.items.length
    if (pendingRef.current || offset >= stream.total) return
    pendingRef.current = true
    try {
      const page = await fetchMessages(transcriptId, { offset, limit: PAGE_SIZE })
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
  }, [stream, transcriptId])

  // NOTE(claude): totalCount is the WINDOW length, not the archive total — deliberate
  // deviation from the plan's literal "totalCount=response.total". With firstItemIndex set,
  // virtuoso renders absolute indexes [firstItemIndex, firstItemIndex + totalCount); passing
  // the archive total would declare unloaded ghost rows from the window's end all the way to
  // the archive's end, and endReached would fire only past the LAST ghost row — never
  // appending. The archive total lives in stream.total and is what stops loadAfter at the
  // boundary.
  return (
    <Virtuoso
      style={{ height: '100%' }}
      totalCount={stream.items.length}
      firstItemIndex={stream.firstItemIndex}
      initialTopMostItemIndex={initialTopMostItemIndex}
      startReached={loadBefore}
      endReached={loadAfter}
      itemContent={(absoluteIndex) => {
        const message = stream.items[absoluteIndex - stream.firstItemIndex]
        if (!message) return null
        const isTarget = message.record_uuid === initialAroundUuid
        return (
          <div
            data-record-uuid={message.record_uuid}
            ref={isTarget ? glowTarget : undefined}
            style={{ padding: '0 24px' }}
          >
            <MessageTurn message={message} />
          </div>
        )
      }}
    />
  )
}

function Calm({ children }: { children: ReactNode }) {
  return <p style={{ color: 'var(--mist)', fontSize: 13, padding: '10px 24px' }}>{children}</p>
}
