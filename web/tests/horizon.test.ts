// The horizon band maps a session onto the reader's LOCAL day, so getHours()/getMinutes()
// must resolve to a known timezone. America/Chicago is UTC-5 in July (CDT); every ISO
// input below carries an explicit offset so each instant is unambiguous and the local-day
// conversion is deterministic on any machine. Expected outputs are anchored to the approved
// mockup (docs/design/2026-07-13-still-water-mockup.html), not recomputed here.
//
// NOTE: ESM hoists the `import` below above this assignment, but horizon.ts reads the zone
// lazily — only when sliceFor runs Date operations — so the ordering is safe.
process.env.TZ = 'America/Chicago'

import { describe, expect, it } from 'vitest'
import { sliceFor } from '../src/lib/horizon'

/** Leading percentage of a "N% ..." CSS value. */
function num(pctValue: string): number {
  return parseFloat(pctValue)
}

describe('sliceFor — day-cycle mapping', () => {
  it('maps the mockup reference session 14:12→02:47 to size 190.7% / position 124.4%', () => {
    const slice = sliceFor('2026-07-13T14:12:00-05:00', '2026-07-14T02:47:00-05:00')
    expect(slice).not.toBeNull()
    expect(Math.abs(num(slice!.size) - 190.7)).toBeLessThanOrEqual(0.15)
    expect(Math.abs(num(slice!.position) - 124.4)).toBeLessThanOrEqual(0.15)
  })

  it('sails a midnight-crossing 22:00→01:30 span to position ≈107.3% through the wrap', () => {
    const slice = sliceFor('2026-07-13T22:00:00-05:00', '2026-07-14T01:30:00-05:00')
    expect(Math.abs(num(slice!.position) - 107.3)).toBeLessThanOrEqual(0.15)
  })

  it('maps a same-day morning 05:00→09:00 session by the formula (size 600%, position 25%)', () => {
    // a = 5/24, d = 4/24 → size = 100/d = 600%, position = (a/(1−d))·100 = 25%.
    const slice = sliceFor('2026-07-13T05:00:00-05:00', '2026-07-13T09:00:00-05:00')
    expect(num(slice!.size)).toBeCloseTo(600, 5)
    expect(num(slice!.position)).toBeCloseTo(25, 5)
  })

  it('applies the 30-minute floor (d = 1/48) to a sub-30-minute session', () => {
    // 10 real minutes floored to 30 → size = 100 / (1/48) = 4800%.
    const slice = sliceFor('2026-07-13T12:00:00-05:00', '2026-07-13T12:10:00-05:00')
    expect(num(slice!.size)).toBeCloseTo(4800, 5)
  })

  it('fills the whole gradient period for spans of a full day or more', () => {
    const slice = sliceFor('2026-07-13T09:00:00-05:00', '2026-07-14T15:00:00-05:00')
    expect(slice).toEqual({ size: '100%', position: '0%' })
  })

  it('returns null when end precedes start', () => {
    expect(sliceFor('2026-07-13T09:00:00-05:00', '2026-07-13T08:00:00-05:00')).toBeNull()
  })

  it('returns null for missing or invalid timestamps', () => {
    expect(sliceFor(null, '2026-07-13T09:00:00-05:00')).toBeNull()
    expect(sliceFor('2026-07-13T09:00:00-05:00', null)).toBeNull()
    expect(sliceFor('not-a-date', '2026-07-13T09:00:00-05:00')).toBeNull()
    expect(sliceFor('', '')).toBeNull()
  })
})
