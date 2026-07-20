import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { useSessionTitle } from '../api/hooks'
import type { SessionSummary } from '../api/types'
import { archiveTitle, displayTitle, prefillTitle } from '../lib/titles'

export interface TitleEditorProps {
  session: SessionSummary
}

// The esc mechanism (plan critique F5, binding — DELIBERATELY a DIFFERENT machine from
// ProjectFilterBar's ref-timestamp double-tap; do not merge the two). There, single-esc never
// closes anything. Here, the FIRST esc closes the editor immediately (instant cancel feel) AND
// installs a document-level keydown listener that self-removes after ESC_CLEAR_WINDOW_MS. A
// second Escape arriving through THAT listener upgrades the cancel into a clear (`title: ''`,
// which the server interprets as revert-to-archive-titles). A closed editor's own keydown
// handler is unmounted by the time the second press could arrive, which is exactly why the
// listener has to live at the document level instead of on the input.
//
// A mousedown ANYWHERE cancels the pending listener outright (not just the timeout) — this is a
// deliberate two-key CHORD, not a bare "any escape within 400ms" timer: any other interaction in
// between (the "click elsewhere" case) means the user moved on, so the second esc must start
// fresh rather than reach back and clear a title the user never asked to touch.
const ESC_CLEAR_WINDOW_MS = 400

const H1_STYLE: CSSProperties = {
  margin: 0,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
}

const TITLE_TEXT_STYLE: CSSProperties = {
  fontFamily: 'var(--serif)',
  fontSize: 22,
  fontWeight: 600,
  lineHeight: 1.25,
  color: 'var(--moonpaper)',
}

// A button styled to read as the h1's text itself — no chrome — mirroring ConversationView's
// LINK_BUTTON_STYLE precedent (a real <button>, not a div[role=button], so Enter/Space work for
// free via native semantics).
const TITLE_BUTTON_STYLE: CSSProperties = {
  ...TITLE_TEXT_STYLE,
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  textAlign: 'left',
}

// Background/border/text come from the shared `.sw-input` class (§9 amendment 2026-07-20); the
// serif TITLE_TEXT_STYLE spread supplies the title typography (and re-states the same moonpaper
// text color the class sets). Layout (radius, padding, width bounds) stays here.
const TITLE_INPUT_STYLE: CSSProperties = {
  ...TITLE_TEXT_STYLE,
  borderRadius: 6,
  padding: '2px 8px',
  minWidth: 280,
  maxWidth: 480,
}

// Small, unobtrusive mist dot marking a renamed session; its title= attribute (a native
// tooltip) carries the archive's original so the rename is never destructive-feeling.
const EDITED_DOT_STYLE: CSSProperties = {
  display: 'inline-block',
  width: 6,
  height: 6,
  borderRadius: '50%',
  background: 'var(--mist)',
  flexShrink: 0,
}

const PROBLEM_STYLE: CSSProperties = {
  fontFamily: 'var(--sans)',
  fontSize: 12,
  color: 'var(--ember)',
}

interface EscSession {
  keyHandler: (event: KeyboardEvent) => void
  cancelHandler: () => void
  timeoutId: ReturnType<typeof setTimeout>
}

/** Session-header title, click-to-edit (§14.3). Click (or Enter/Space on the button) opens an
 * input pre-filled with the CURRENT DISPLAY title; Enter commits, blur commits-if-changed, a
 * single Escape cancels instantly, and a second Escape within 400ms of the first clears the
 * user title entirely (see the esc-mechanism note above). A mist dot marks a renamed session. */
export function TitleEditor({ session }: TitleEditorProps) {
  const mutation = useSessionTitle()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  // The value editing started FROM — handleBlur's "commits only if-changed" check compares
  // against this snapshot, not a freshly recomputed prefillTitle(session) (the session prop is
  // stable for the lifetime of one edit anyway, but a snapshot is the honest source of truth).
  const originalRef = useRef('')
  // Guards the Enter-then-blur double-commit: Enter sets this synchronously (before any
  // microtask), so a blur that follows — whether because the browser naturally blurs on Enter or
  // because the mutation's onSuccess unmounts the input — can never fire a second mutate call.
  const committedRef = useRef(false)
  const escSessionRef = useRef<EscSession | null>(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  // Unmount-only cleanup (navigating away mid-window must not leave a dangling document
  // listener). Inlined rather than calling the named clearEscSession helper below so this
  // effect's empty dep array stays honest — the only external state it touches is the ref
  // itself, which is stable across renders by definition.
  useEffect(() => {
    return () => {
      const pending = escSessionRef.current
      if (!pending) return
      document.removeEventListener('keydown', pending.keyHandler)
      document.removeEventListener('mousedown', pending.cancelHandler)
      clearTimeout(pending.timeoutId)
      escSessionRef.current = null
    }
  }, [])

  function clearEscSession() {
    const pending = escSessionRef.current
    if (!pending) return
    document.removeEventListener('keydown', pending.keyHandler)
    document.removeEventListener('mousedown', pending.cancelHandler)
    clearTimeout(pending.timeoutId)
    escSessionRef.current = null
  }

  function installEscSession() {
    clearEscSession()
    const keyHandler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      clearEscSession()
      mutation.mutate({ uuid: session.session_uuid, title: '' })
    }
    const cancelHandler = () => clearEscSession()
    const timeoutId = setTimeout(clearEscSession, ESC_CLEAR_WINDOW_MS)
    document.addEventListener('keydown', keyHandler)
    document.addEventListener('mousedown', cancelHandler)
    escSessionRef.current = { keyHandler, cancelHandler, timeoutId }
  }

  function openEditor() {
    clearEscSession()
    setProblem(null)
    const initial = prefillTitle(session)
    originalRef.current = initial
    setDraft(initial)
    committedRef.current = false
    setEditing(true)
  }

  function commit(value: string) {
    if (committedRef.current) return
    committedRef.current = true
    setProblem(null)
    mutation.mutate(
      { uuid: session.session_uuid, title: value },
      {
        onSuccess: () => setEditing(false),
        onError: (error) => {
          committedRef.current = false
          setProblem(error instanceof ApiError ? error.detail : 'Could not save the title.')
        },
      },
    )
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      commit(draft)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      // stopPropagation is load-bearing, not defensive: this Escape keydown is still bubbling
      // (React handlers run mid-bubble), and installEscSession is about to attach a REAL
      // document-level listener. Without stopping propagation here, this SAME event would
      // continue bubbling past the point of attachment and immediately self-trigger the
      // listener it just installed, misreading press #1 as press #2 and clearing the title on a
      // single Escape.
      event.stopPropagation()
      setEditing(false)
      installEscSession()
    }
  }

  function handleBlur() {
    if (committedRef.current) return
    if (draft === originalRef.current) {
      setEditing(false)
      return
    }
    commit(draft)
  }

  if (editing) {
    return (
      <h1 style={H1_STYLE}>
        <input
          ref={inputRef}
          className="sw-input"
          aria-label="Session title"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          style={TITLE_INPUT_STYLE}
        />
        {problem && <span style={PROBLEM_STYLE}>{problem}</span>}
      </h1>
    )
  }

  return (
    <h1 style={H1_STYLE}>
      <button type="button" onClick={openEditor} style={TITLE_BUTTON_STYLE}>
        {displayTitle(session)}
      </button>
      {session.user_title !== null && (
        <span
          className="title-edited-dot"
          aria-hidden="true"
          title={archiveTitle(session)}
          style={EDITED_DOT_STYLE}
        />
      )}
    </h1>
  )
}
