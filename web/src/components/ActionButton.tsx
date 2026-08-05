import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import './action-button.css'

// Dep-free port of the AIconButton state machine from relativityboy/mui-action-buttons:
// idle → pending → success-flash (2s, auto-clear) | sticky error (click 1 dismisses without
// re-firing, click 2 retries). Ported WITHOUT three upstream defects (spec §2): the error path
// scheduled a stray success-clear timer, post-await state writes weren't unmount-safe, and the
// error carried no message. Status lives INSIDE the button — no external status row.
const FLASH_MS = 2000

type Phase =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'success'; label: string }
  | { kind: 'error'; message: string }

export interface ActionButtonProps {
  /** Leading glyph (its own span so the pending spin targets it alone). */
  glyph: string
  /** Idle label; also the stem of the default flash (`{text} ✓`) and error (`⚠ {text} failed`). */
  text: string
  /** Resolved string overrides the success-flash label; a throw enters the sticky error state. */
  onClick: () => Promise<string | void>
  style?: CSSProperties
  className?: string
  title?: string
}

export function ActionButton({ glyph, text, onClick, style, className, title }: ActionButtonProps) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const mountedRef = useRef(true)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    mountedRef.current = true // StrictMode remount re-arms the flag after the cleanup below
    return () => {
      mountedRef.current = false
      if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current)
    }
  }, [])

  async function handleClick() {
    if (phase.kind === 'pending') return
    if (phase.kind === 'error') {
      setPhase({ kind: 'idle' }) // dismiss only — the next click retries
      return
    }
    if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current)
    setPhase({ kind: 'pending' })
    try {
      const label = await onClick()
      if (!mountedRef.current) return
      setPhase({ kind: 'success', label: typeof label === 'string' ? label : `${text} ✓` })
      flashTimerRef.current = setTimeout(() => {
        flashTimerRef.current = null
        setPhase({ kind: 'idle' })
      }, FLASH_MS)
    } catch (e) {
      if (!mountedRef.current) return
      setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
    }
  }

  const isError = phase.kind === 'error'
  const label =
    phase.kind === 'success' ? phase.label
    : isError ? `⚠ ${text} failed`
    : phase.kind === 'pending' ? `${text}…` // the ellipsis IS the reduced-motion pending marker
    : text
  const classes = ['action-button', 'mono', className, phase.kind !== 'idle' ? `is-${phase.kind}` : null]
    .filter(Boolean).join(' ')

  return (
    <button
      type="button"
      className={classes}
      style={style}
      onClick={handleClick}
      aria-busy={phase.kind === 'pending' || undefined}
      title={isError ? phase.message : title}
      aria-label={isError ? `${text} — failed: ${phase.message}` : undefined}
    >
      <span className="action-button-glyph">{glyph}</span> {label}
    </button>
  )
}
