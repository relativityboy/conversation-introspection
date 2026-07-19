/**
 * Horizon-band geometry: maps a session's real span onto the Still Water day-cycle
 * gradient (docs/design/2026-07-13-still-water-mockup.html, the `--daycycle` stops —
 * transcribed verbatim into src/theme.css). This module owns only the size/position of the
 * gradient window; the gradient itself lives in the component/theme.
 *
 * NOTE(claude): API timestamps arrive as UTC ISO-8601, but the band answers "when in MY
 * day did this session live?" — an afternoon-into-midnight session must read as afternoon
 * light sailing into deep blue in the *reader's* timezone, whatever offset it was recorded
 * at. So `a` (the start-of-day fraction) is taken from LOCAL clock components via
 * Date#getHours/getMinutes/getSeconds, which resolve in the host timezone (pinned with
 * process.env.TZ in tests). Duration `d`, by contrast, is a physical elapsed span, so it is
 * the absolute millisecond difference — timezone- and DST-independent.
 */

export interface HorizonSlice {
  /** CSS background-size x-value, e.g. "190.7285%". */
  size: string
  /** CSS background-position x-value, e.g. "124.3796%". */
  position: string
}

const DAY_SECONDS = 86_400
const DAY_MS = 86_400_000

/** 30-minute visual floor for `d`, expressed as a fraction of a day (30 min / 24 h). */
const MIN_DURATION = 1 / 48

/**
 * The math (fixed contract — do not re-derive): with `a` = start as a fraction of the local
 * day [0,1) and `d` = duration as a fraction of a day (floored to MIN_DURATION):
 *   background-size     = (100 / d)%
 *   background-position = (a / (1 − d)) · 100%
 * Midnight crossers need no special-casing — the gradient's repeat-x wrap carries `a`
 * past the day boundary and back into deep blue. A span of a full day or more fills exactly
 * one gradient period.
 *
 * Returns null for missing, unparseable, or reversed (end < start) timestamps; the
 * component renders nothing in that case.
 */
export function sliceFor(startISO: string | null, endISO: string | null): HorizonSlice | null {
  if (!startISO || !endISO) return null

  const start = new Date(startISO)
  const end = new Date(endISO)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null
  if (end.getTime() < start.getTime()) return null

  const a =
    (start.getHours() * 3600 + start.getMinutes() * 60 + start.getSeconds()) / DAY_SECONDS
  const d = Math.max((end.getTime() - start.getTime()) / DAY_MS, MIN_DURATION)

  if (d >= 1) return { size: '100%', position: '0%' }

  return {
    size: pct(100 / d),
    position: pct((a / (1 - d)) * 100),
  }
}

/** Format a percentage: round to 4 decimals and trim trailing zeros so CSS stays tidy. */
function pct(value: number): string {
  return `${Number(value.toFixed(4))}%`
}
