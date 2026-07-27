import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChatOnlyToggle } from '../src/components/reader/ChatOnlyToggle'

// Regression: the toggle's resting outline vanished after the first use. ACTIVE_STYLE used to
// spread BASE_STYLE's `border` SHORTHAND and then add a `borderColor` LONGHAND. Going back to
// BASE_STYLE, React removes the longhand it no longer sees in the new style object and does not
// re-apply the (unchanged) shorthand -- so border-color was left cleared and Chrome painted the
// resting border black instead of var(--shore). Measured in a real browser across four
// consecutive toggles; on the dark ground a #1a2740 outline going black just disappears, so the
// inactive button stopped looking the way it did on page load. Fix: both style objects carry only
// the shorthand, which React diffs cleanly.
describe('ChatOnlyToggle border styling', () => {
  it('still specifies a resting border colour after being toggled off', () => {
    const noop = () => {}
    const { rerender } = render(<ChatOnlyToggle chatOnly setChatOnly={noop} />)
    const button = screen.getByRole('button', { name: 'conversation only' })
    expect(button.getAttribute('style')).toContain('border: 1px solid var(--dragonfly)')

    rerender(<ChatOnlyToggle chatOnly={false} setChatOnly={noop} />)
    expect(button.getAttribute('aria-pressed')).toBe('false')
    // The whole regression: coming back from active, the resting border must still name --shore.
    expect(button.getAttribute('style')).toContain('border: 1px solid var(--shore)')
  })
})
