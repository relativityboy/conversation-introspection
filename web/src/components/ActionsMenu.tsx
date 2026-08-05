import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useResumeSession } from '../api/hooks'
import type { ResumeResult, SessionDetail } from '../api/types'
import { ActionButton } from './ActionButton'
import { ArchiveButton } from './ArchiveButton'

// The header meta row's actions ▾ menu (spec §3.1): resume, the .jsonl export, and archive
// relocate from three standalone controls into one panel behind a single trigger, closed by
// default. `statusText` moved verbatim from the now-retired standalone resume control --
// fallback statuses keep the exact resume command readable/selectable, the room never swallows
// what it knows (spec §17.3/§17.4).
function statusText(r: ResumeResult): string {
  const prefix = r.restored ? 'restored from archive · ' : ''
  switch (r.mode) {
    case 'launched':
      return `${prefix}launched`
    case 'missing_cwd':
      return `${prefix}original directory missing (${r.detail}) — run: ${r.command}`
    case 'open_failed':
      return `${prefix}couldn't open terminal (${r.detail}) — run: ${r.command}`
    case 'unsupported_platform':
      return `${prefix}launch is macOS-only — run: ${r.command}`
    default: {
      const exhausted: never = r.mode
      return exhausted
    }
  }
}

// Mirrors ChatOnlyToggle's inactive pill: mono 11px, 1px solid var(--shore), radius 999, mist on
// transparent.
const TRIGGER_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  border: '1px solid var(--shore)',
  borderRadius: 999,
  padding: '3px 10px',
  background: 'transparent',
  cursor: 'pointer',
  color: 'var(--mist)',
}

// The ProjectFilterBar listbox surface (same floating-panel vocabulary as the project filter's
// combo popup).
const PANEL_STYLE: CSSProperties = {
  position: 'absolute',
  top: 'calc(100% + 4px)',
  left: 0,
  zIndex: 20,
  minWidth: 200,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'flex-start',
  gap: 8,
  padding: '10px 12px',
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  boxShadow: '0 6px 20px rgba(0,0,0,.35)',
}

// The retired standalone resume control's button style: bare, dragonfly, mono 11 -- shared by
// every item in the panel (resume, .jsonl, archive) so the menu reads as one calm list.
const ITEM_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: 'var(--dragonfly)',
}

const LINK_ITEM_STYLE: CSSProperties = {
  color: 'var(--dragonfly)',
  textDecoration: 'none',
  fontFamily: 'var(--mono)',
  fontSize: 11,
}

const DETAIL_STYLE: CSSProperties = {
  color: 'var(--mist)',
  fontSize: 11,
  userSelect: 'text',
  maxWidth: 320,
}

export interface ActionsMenuProps {
  session: SessionDetail
  backSearch: string
}

/** Spec §3.1: the header's actions ▾ menu -- resume, the .jsonl export, and archive relocate
 * behind one trigger, closed by default. Resume's degradation (non-'launched' modes) is an
 * HTTP-200 result carrying a runnable command; it surfaces BOTH as the ActionButton's sticky
 * error AND as a selectable `.resume-detail` line inside the panel with the full statusText
 * sentence -- the command must stay readable, never vanish in a flash or hide in a hover title
 * (§17.3, amended). */
export function ActionsMenu({ session, backSearch }: ActionsMenuProps) {
  const [open, setOpen] = useState(false)
  const [resumeDetail, setResumeDetail] = useState<string | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const mutation = useResumeSession()

  // Click-outside via document mousedown + containment — deliberately NOT ProjectFilterBar's
  // blur pattern: this panel must survive focus loss while a resume is pending (spec §3).
  // Listener exists only while open; removed on close/unmount. Distinct from the TitleEditor
  // and ProjectFilterBar esc machines per the house rule — Escape here is element-scoped below.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  async function runResume(): Promise<string | void> {
    setResumeDetail(null)
    const r = await mutation.mutateAsync(session.session_uuid)
    if (r.mode === 'launched') return r.restored ? 'restored & resumed ✓' : 'resumed ✓'
    setResumeDetail(statusText(r)) // the full sentence with the runnable command — §17.3: the
    throw new Error('launch degraded — see menu for the command') // room never swallows what it knows
  }

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div
      ref={wrapRef}
      style={{ position: 'relative' }}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation()
          close()
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className="actions-trigger mono"
        aria-expanded={open}
        aria-controls="session-actions"
        onClick={() => (open ? close() : setOpen(true))}
        style={TRIGGER_STYLE}
      >
        actions ▾
      </button>
      {open && (
        <div id="session-actions" className="actions-panel" style={PANEL_STYLE}>
          <ActionButton
            glyph="⟲"
            text={session.on_disk ? 'resume' : 'restore & resume'}
            onClick={runResume}
            style={ITEM_STYLE}
          />
          {resumeDetail && (
            <span className="resume-detail mono" style={DETAIL_STYLE}>
              {resumeDetail}
            </span>
          )}
          {/* The archive's headline capability, one glance from every conversation: the raw
            records back out as JSONL. Plain <a> (not router Link) -- it's an API endpoint. */}
          <a
            href={`/api/v1/sessions/${session.session_uuid}/export.jsonl`}
            style={LINK_ITEM_STYLE}
          >
            ↓ .jsonl
          </a>
          <ArchiveButton sessionUuid={session.session_uuid} backSearch={backSearch} />
        </div>
      )}
    </div>
  )
}
