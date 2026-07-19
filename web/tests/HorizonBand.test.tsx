// TZ pinned so the caption renders known LOCAL times. See tests/horizon.test.ts for the
// import-hoisting / lazy-zone-read reasoning; the same applies here.
process.env.TZ = 'America/Chicago'

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HorizonBand } from '../src/components/HorizonBand'

// The mockup's reference session: 14:12 → 02:47 local, 12h 35m.
const REF_START = '2026-07-13T14:12:00-05:00'
const REF_END = '2026-07-14T02:47:00-05:00'

describe('HorizonBand', () => {
  it('renders a caption of local times and duration for the full variant', () => {
    render(<HorizonBand start={REF_START} end={REF_END} variant="full" />)
    expect(screen.getByText('14:12 → 02:47 · 12h 35m')).toBeDefined()
  })

  it('applies the computed gradient size and position to the band', () => {
    const { container } = render(<HorizonBand start={REF_START} end={REF_END} variant="full" />)
    const band = container.querySelector('.horizon-band') as HTMLElement
    expect(Math.abs(parseFloat(band.style.backgroundSize) - 190.7)).toBeLessThanOrEqual(0.15)
    expect(Math.abs(parseFloat(band.style.backgroundPosition) - 124.4)).toBeLessThanOrEqual(0.15)
  })

  it('renders the micro variant with no caption and the approved recessed opacity', () => {
    const { container } = render(<HorizonBand start={REF_START} end={REF_END} variant="micro" />)
    expect(screen.queryByText(/→/)).toBeNull()
    const band = container.querySelector('.horizon-band') as HTMLElement
    expect(band.style.opacity).toBe('0.55')
  })

  it('renders nothing when the session has no start time', () => {
    const { container } = render(<HorizonBand start={null} end={REF_END} variant="full" />)
    expect(container.firstChild).toBeNull()
  })
})
