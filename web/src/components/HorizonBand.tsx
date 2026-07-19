import type { CSSProperties } from 'react'
import { sliceFor } from '../lib/horizon'

export interface HorizonBandProps {
  /** Session start as UTC ISO-8601 (or null when unknown). */
  start: string | null
  /** Session end as UTC ISO-8601 (or null when unknown). */
  end: string | null
  /**
   * 'full': 8px band with a right-aligned mono caption above.
   * 'micro': 2px band, no caption, recessed to opacity 0.55 (the approved quieting of the
   * mockup's over-loud list-item micro-bands).
   */
  variant: 'full' | 'micro'
}

const MS_PER_MINUTE = 60_000

// The gradient stops live in src/theme.css as `--daycycle`, transcribed verbatim from the
// mockup. Referencing the token (as the mockup's own `.band` rule does) keeps a single
// source of truth for the day-cycle colors; this component owns only how that gradient is
// windowed (size/position from sliceFor) and repeated.
export function HorizonBand({ start, end, variant }: HorizonBandProps) {
  const slice = sliceFor(start, end)
  if (!slice) return null

  const full = variant === 'full'
  const bandStyle: CSSProperties = {
    height: full ? 8 : 2,
    borderRadius: full ? 2 : 1,
    backgroundImage: 'var(--daycycle)',
    backgroundRepeat: 'repeat-x',
    backgroundSize: `${slice.size} 100%`,
    backgroundPosition: `${slice.position} 0`,
    ...(full ? null : { opacity: 0.55 }),
  }

  if (!full) {
    return <div className="horizon-band" aria-hidden="true" style={bandStyle} />
  }

  // start/end are guaranteed non-null: sliceFor returned a slice, which requires both.
  const text = caption(start!, end!)
  return (
    <div className="horizon-wrap">
      <div
        className="horizon-caption"
        style={{
          fontFamily: 'var(--mono)',
          fontSize: 11,
          color: 'var(--mist)',
          textAlign: 'right',
          marginBottom: 5,
        }}
      >
        {text}
      </div>
      <div className="horizon-band" aria-hidden="true" style={bandStyle} title={text} />
    </div>
  )
}

/** "HH:MM → HH:MM · Nh Nm" — local 24h times, physical elapsed duration. */
function caption(startISO: string, endISO: string): string {
  const start = new Date(startISO)
  const end = new Date(endISO)
  const minutes = Math.round((end.getTime() - start.getTime()) / MS_PER_MINUTE)
  const hours = Math.floor(minutes / 60)
  return `${hhmm(start)} → ${hhmm(end)} · ${hours}h ${minutes % 60}m`
}

function hhmm(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function pad(n: number): string {
  return n.toString().padStart(2, '0')
}
