import type { CSSProperties } from 'react'
import { useResumeSession } from '../api/hooks'
import type { ResumeResult } from '../api/types'

// Header meta-row sibling of ArchiveButton/the .jsonl link — same mono 11px voice. The button is
// dragonfly (it's an action, like the export link); the status text is mist. Fallback statuses
// keep the exact resume command readable/selectable — the room never swallows what it knows
// (spec §17.3/§17.4).
const BUTTON_STYLE: CSSProperties = {
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

const STATUS_STYLE: CSSProperties = {
  color: 'var(--mist)',
  userSelect: 'text',
}

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

export interface ResumeButtonProps {
  sessionUuid: string
  /** SessionDetail.on_disk — honest label before the click (§17.4). */
  onDisk: boolean
}

/** Spec §17: the door back into a conversation. POST → terminal opens with `claude --resume`
 * running; every degradation is reported in place, command included. */
export function ResumeButton({ sessionUuid, onDisk }: ResumeButtonProps) {
  const mutation = useResumeSession()
  const status = mutation.isError
    ? 'resume failed'
    : mutation.data
      ? statusText(mutation.data)
      : null

  return (
    <>
      <button
        type="button"
        className="resume-button mono"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate(sessionUuid)}
        style={BUTTON_STYLE}
      >
        {onDisk ? '⟲ resume' : '⟲ restore & resume'}
      </button>
      {status && (
        <span className="resume-status" style={STATUS_STYLE}>
          {status}
        </span>
      )}
    </>
  )
}
