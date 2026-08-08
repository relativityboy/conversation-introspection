import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useRawRecord } from '../../api/hooks'
import type { MessageOut } from '../../api/types'
import { isVisibleInView, type ViewMode } from '../../lib/viewMode'
import { MarkdownProse } from './MarkdownProse'
import { ViewToggle } from './ViewToggle'

// Still Water styling: a depth-toned backdrop over the reader, a lifted surface panel, mono
// content. The fade-in lives on the `.raw-record-overlay` CSS class (theme.css) so reduced-motion
// is honored there, not re-implemented in JS.
const OVERLAY_STYLE: CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 50,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 24,
  background: 'rgba(11, 18, 32, 0.72)', // --depth at low alpha
}

const PANEL_STYLE: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  width: 'min(760px, 100%)',
  maxHeight: '84vh',
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  borderRadius: 10,
  outline: 'none',
  overflow: 'hidden',
}

const HEADER_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  padding: '12px 16px',
  borderBottom: '1px solid var(--shore)',
}

const UUID_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  color: 'var(--mist)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const NAV_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '10px 16px',
  borderBottom: '1px solid var(--shore)',
}

const ICON_BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  lineHeight: 1.2,
  background: 'none',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  padding: '3px 9px',
  cursor: 'pointer',
  color: 'var(--mist)',
}

const CLOSE_BUTTON_STYLE: CSSProperties = {
  ...ICON_BUTTON_STYLE,
  border: 'none',
  fontSize: 16,
  padding: '0 4px',
}

function toggleStyle(active: boolean): CSSProperties {
  return active
    ? { ...ICON_BUTTON_STYLE, color: 'var(--dragonfly)', borderColor: 'var(--dragonfly)' }
    : ICON_BUTTON_STYLE
}

const CONTENT_STYLE: CSSProperties = {
  padding: '12px 16px',
  overflow: 'auto',
  minHeight: 0,
}

const PRE_STYLE: CSSProperties = {
  margin: 0,
  fontFamily: 'var(--mono)',
  fontSize: 12,
  lineHeight: 1.5,
  color: 'var(--moonpaper)',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
}

const CLASSIFICATION_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  color: 'var(--mist)',
  padding: '8px 16px 0',
}

const NOTE_STYLE: CSSProperties = { color: 'var(--mist)', fontSize: 13, margin: 0 }
const NOTICE_STYLE: CSSProperties = {
  color: 'var(--dawn)',
  fontSize: 12,
  fontFamily: 'var(--sans)',
  margin: '0 0 8px',
}

// Focusable descendants for the Tab trap. The panel itself (tabIndex=-1) is excluded by the
// [tabindex="-1"] filter, so Tab cycles only the real controls.
const FOCUSABLE = 'button:not([disabled]), [href], input, [tabindex]:not([tabindex="-1"])'

/** The §6 classification line: `authorship: {kind} ({basis}{ — detail if present})`. A null
 * `authorship_kind` means the record predates the reparse backfill (spec §4 deploy window), not
 * that it was classified as blank — that gets its own explicit sentence, not an empty parens. */
function classificationLine(message: MessageOut | undefined): string {
  const kind = message?.authorship_kind ?? null
  if (kind == null) return 'authorship: not yet classified (reparse pending)'
  const basis = message?.authorship_basis ?? ''
  const detail = message?.authorship_detail
  return `authorship: ${kind} (${basis}${detail ? ` — ${detail}` : ''})`
}

export interface RawRecordInspectorProps {
  /** The reader's loaded window in traversal order (MessageStream's `stream.items`). Navigation
   * stays inside this window — the modal never triggers edge-fetches (spec §15.2, kept simple):
   * at a window edge the corresponding arrow disables. */
  items: MessageOut[]
  /** The record to open on (the row whose `{}` button was clicked). */
  initialUuid: string
  /** Seeds the in-modal view filter — "follow parent" by default. Changing it in the modal
   * narrows/widens which rows prev/next visits, independently of the reader's own mode. */
  parentView: ViewMode
  onClose: () => void
}

/** The §15.2 raw-record inspector: a reader-level modal (single instance, keyed by the current
 * record) showing a record's exact stored `raw_line`. Pretty-prints the JSON with a "raw bytes"
 * toggle (falling back to the raw text with a notice when the line isn't valid JSON); ◀/▶ and
 * Left/Right navigate within the loaded window in the reader's traversal order, skipping rows the
 * current view filter hides. Esc closes; focus is trapped while open and returned to the opening
 * button on close. */
export function RawRecordInspector({
  items,
  initialUuid,
  parentView,
  onClose,
}: RawRecordInspectorProps) {
  const [currentUuid, setCurrentUuid] = useState(initialUuid)
  const [filterView, setFilterView] = useState<ViewMode>(parentView)
  const [showRaw, setShowRaw] = useState(false)

  const rootRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)

  // Capture the element that opened us (the row's `{}` button, focused by the click), move focus
  // into the modal, and restore it on close — the accessible modal contract. Runs once: the modal
  // is a single instance whose current record changes via state, not a remount, so navigation
  // never re-captures or re-steals focus.
  useEffect(() => {
    openerRef.current = (document.activeElement as HTMLElement) ?? null
    rootRef.current?.focus()
    return () => {
      openerRef.current?.focus?.()
    }
  }, [])

  // The current record's own MessageOut, looked up from the loaded window — carries the three
  // authorship_* fields the classification line below reads. Always found in practice: currentUuid
  // only ever changes to a uuid drawn from `navigable`, itself filtered from `items`.
  const currentMessage = items.find((m) => m.record_uuid === currentUuid)

  const navigable = useMemo(
    () => items.filter((m) => isVisibleInView(m, filterView)),
    [items, filterView],
  )
  // -1 when the current record is filtered OUT of `navigable` (e.g. the filter was toggled ON
  // while sitting on a now-hidden row): both arrows disable, but the record itself still shows.
  const currentIndex = navigable.findIndex((m) => m.record_uuid === currentUuid)
  const hasPrev = currentIndex > 0
  const hasNext = currentIndex !== -1 && currentIndex < navigable.length - 1

  const query = useRawRecord(currentUuid)

  const goPrev = () => {
    if (hasPrev) setCurrentUuid(navigable[currentIndex - 1].record_uuid)
  }
  const goNext = () => {
    if (hasNext) setCurrentUuid(navigable[currentIndex + 1].record_uuid)
  }

  function trapTab(event: ReactKeyboardEvent<HTMLDivElement>) {
    const root = rootRef.current
    if (!root) return
    const focusables = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]
    if (focusables.length === 0) {
      event.preventDefault()
      return
    }
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  // Hotkeys live on the modal element, NOT on document — so a closed modal (unmounted) has no
  // handler and the arrows/Esc simply don't fire, and an Esc here can't collide with the
  // document-level esc machines (TitleEditor / ProjectFilterBar). stopPropagation is the belt to
  // that suspenders: it keeps this Esc from reaching TitleEditor's transient document listener.
  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    switch (event.key) {
      case 'Escape':
        event.stopPropagation()
        onClose()
        break
      case 'ArrowLeft':
        event.stopPropagation()
        event.preventDefault()
        goPrev()
        break
      case 'ArrowRight':
        event.stopPropagation()
        event.preventDefault()
        goNext()
        break
      case 'Tab':
        trapTab(event)
        break
    }
  }

  return (
    <div
      className="raw-record-overlay"
      style={OVERLAY_STYLE}
      onKeyDown={handleKeyDown}
      onMouseDown={(event) => {
        // Click on the backdrop itself (not a child) closes — the panel's own mousedowns don't
        // bubble a close because their target is inside the panel, not the overlay.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={rootRef}
        role="dialog"
        aria-modal="true"
        aria-label="Raw record"
        tabIndex={-1}
        style={PANEL_STYLE}
      >
        <div style={HEADER_STYLE}>
          <span className="mono" style={UUID_STYLE} title={currentUuid}>
            {currentUuid}
          </span>
          <button type="button" aria-label="Close" onClick={onClose} style={CLOSE_BUTTON_STYLE}>
            ✕
          </button>
        </div>
        <div style={NAV_STYLE}>
          <button
            type="button"
            aria-label="Previous record"
            disabled={!hasPrev}
            onClick={goPrev}
            style={{ ...ICON_BUTTON_STYLE, opacity: hasPrev ? 1 : 0.4 }}
          >
            ◀
          </button>
          <button
            type="button"
            aria-label="Next record"
            disabled={!hasNext}
            onClick={goNext}
            style={{ ...ICON_BUTTON_STYLE, opacity: hasNext ? 1 : 0.4 }}
          >
            ▶
          </button>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            aria-pressed={showRaw}
            onClick={() => setShowRaw((v) => !v)}
            style={toggleStyle(showRaw)}
          >
            raw bytes
          </button>
          <ViewToggle view={filterView} setView={setFilterView} />
        </div>
        <div className="raw-record-authorship mono" style={CLASSIFICATION_STYLE}>
          {classificationLine(currentMessage)}
        </div>
        <div style={CONTENT_STYLE}>
          <RawContent
            isPending={query.isPending}
            isError={query.isError}
            text={query.data}
            showRaw={showRaw}
          />
        </div>
      </div>
    </div>
  )
}

interface RawContentProps {
  isPending: boolean
  isError: boolean
  text: string | undefined
  showRaw: boolean
}

// Pretty vs raw is a pure function of (text, showRaw): raw shows the exact bytes-as-text; pretty
// JSON.parse+stringify, falling back to the raw text under a notice when the line isn't valid JSON
// (a malformed line the byte-faithful endpoint still returns verbatim).
function RawContent({ isPending, isError, text, showRaw }: RawContentProps) {
  if (isPending) return <p style={NOTE_STYLE}>…</p>
  if (isError || text === undefined)
    return <p style={NOTE_STYLE}>Couldn&rsquo;t load this record.</p>
  if (showRaw) return <pre className="raw-record-bytes mono" style={PRE_STYLE}>{text}</pre>
  let pretty: string
  try {
    pretty = JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return (
      <>
        <p className="raw-record-notice" style={NOTICE_STYLE}>
          Not valid JSON — showing the raw line.
        </p>
        <pre className="raw-record-bytes mono" style={PRE_STYLE}>{text}</pre>
      </>
    )
  }
  // NOTE(claude): the pretty JSON rides the EXISTING MarkdownProse pipeline (react-markdown +
  // rehype-highlight) as a fenced block — zero new deps, and the one Still-Water hljs theme in
  // markdown-prose.css applies untouched (spec §7). The 4-backtick fence cannot be escaped by
  // content: JSON.stringify(…, null, 2) output lines always start with whitespace, a quote, a
  // bracket/brace, a digit/minus, or t/f/n — never a backtick at line start.
  return (
    <div className="raw-record-json">
      <MarkdownProse markdown={'````json\n' + pretty + '\n````'} />
    </div>
  )
}
