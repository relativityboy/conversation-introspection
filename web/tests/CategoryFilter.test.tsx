// Task T17 (owner ruling 2026-09-25) + item-2 revision (same day, owner spec via coordinator) +
// item-3 revision (same day, owner spec via coordinator, after a live-room look): the dropdown
// mechanism itself (open/close, click-away, Escape) is unchanged from the original T17 landing —
// see the git history of this file for that red evidence. Item-2 moved the presets INSIDE the
// panel (above the checkboxes, still configure-and-reflect, still live), reduced the header to
// the trigger alone, and reformatted the trigger label to the exact `View: <label>` shape. Item-3
// restores a decorative ▾ arrow (excluded from the accessible name), widens the reserved trigger
// width with explicit headroom, and gives the trigger a real quiet button appearance shared with
// ActionsMenu's `actions ▾` trigger via one CSS class (`sw-trigger`, theme.css) — see
// tests/ActionsMenu.test.tsx for that trigger's own half of this pin.
import { readFileSync } from 'node:fs'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CategoryFilter } from '../src/components/reader/CategoryFilter'
import { CATEGORY_LABELS, PRESET_SETS, type CategorySlug } from '../src/lib/viewMode'

// The longest of the four possible trigger labels -- "View: chat+harness" -- is the text the
// width formula below is sized around. Computed from the literal here (not imported from the
// source) so this test pins the actual visible string, not just whatever the source happens to
// compute.
const LONGEST_LABEL = 'View: chat+harness'

// Item-3 revision: the reserved width formula, mirrored from the source's own (owner-specified)
// ingredients -- longest label + the decorative arrow + the button's own inner padding + explicit
// headroom ("favor generous over clever" -- the owner's eyes are the instrument for the real
// room; jsdom can't measure real layout, so this pins the FORMULA the source uses, not a
// rendered pixel width).
const ARROW_TEXT = ' ▾'
const PADDING_CH = 2
const HEADROOM_CH = 2
const EXPECTED_TRIGGER_WIDTH = `${LONGEST_LABEL.length + ARROW_TEXT.length + PADDING_CH + HEADROOM_CH}ch`

async function openPanel() {
  await userEvent.click(screen.getByRole('button', { name: /^View: /}))
}

function panel() {
  return screen.getByRole('group', { name: 'message categories' })
}

describe('CategoryFilter — header contains only the trigger', () => {
  it('closed by default: no preset buttons, no checkboxes anywhere in the DOM', () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'chat' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'chat+harness' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'all' })).toBeNull()
    // Exactly one button in the whole component when closed: the trigger.
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })
})

describe('CategoryFilter — trigger label ("View: <label>", exact)', () => {
  it('shows "View: <preset>" for each of the three presets', () => {
    const { rerender } = render(
      <CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'View: chat' })).not.toBeNull()

    rerender(<CategoryFilter selection={PRESET_SETS['chat-harness']} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'View: chat+harness' })).not.toBeNull()

    rerender(<CategoryFilter selection={PRESET_SETS.all} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'View: all' })).not.toBeNull()
  })

  it('shows "View: x / y" for a custom combination', () => {
    render(
      <CategoryFilter
        selection={new Set(['you-chat', 'tool-traffic'])}
        setSelection={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'View: 2 / 5' })).not.toBeNull()
  })
})

describe('CategoryFilter — trigger width (constant across every label, no layout shift)', () => {
  it('reserves an identical, non-empty fixed width for every label state, sized to the longest label plus arrow/padding/headroom', () => {
    const selections: ReadonlySet<CategorySlug>[] = [
      PRESET_SETS.chat,
      PRESET_SETS['chat-harness'],
      PRESET_SETS.all,
      new Set(['you-chat', 'tool-traffic']),
    ]
    const widths = selections.map((selection) => {
      const { unmount, getByRole } = render(
        <CategoryFilter selection={selection} setSelection={vi.fn()} />,
      )
      const width = getByRole('button', { name: /^View: /}).style.width
      unmount()
      return width
    })
    expect(new Set(widths).size).toBe(1)
    expect(widths[0]).toBe(EXPECTED_TRIGGER_WIDTH)
  })
})

describe('CategoryFilter — decorative arrow (item-3 revision, restored)', () => {
  it('renders a ▾ arrow that is excluded from the accessible name', () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    // The exact-match name query below only succeeds if the arrow is NOT part of the accessible
    // name -- an aria-hidden sibling achieves that while still showing the glyph visually.
    const trigger = screen.getByRole('button', { name: 'View: chat' })
    expect(trigger.textContent).toContain('▾')
  })

  it('carries the arrow through every label state, still excluded from the name', () => {
    const { rerender } = render(
      <CategoryFilter selection={PRESET_SETS.all} setSelection={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'View: all' }).textContent).toContain('▾')

    rerender(
      <CategoryFilter selection={new Set(['you-chat', 'tool-traffic'])} setSelection={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'View: 2 / 5' }).textContent).toContain('▾')
  })
})

describe('CategoryFilter — shared quiet button treatment (item-3 revision)', () => {
  it('carries the shared sw-trigger class (the same mechanism ActionsMenu\'s trigger uses — see tests/ActionsMenu.test.tsx)', () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'View: chat' })
    expect(trigger.className.split(' ')).toContain('sw-trigger')
  })

  // jsdom doesn't apply real stylesheets, so a computed-style/pixel assertion here would be
  // meaningless -- this pins the MECHANISM instead: exactly one `.sw-trigger` rule (plus its own
  // :hover/:focus-visible rule) exists in theme.css, using only existing tokens (a --shore
  // border/hover-fill, the app's pill radius idiom) -- proving one shared definition, not a
  // per-consumer copy that could drift.
  it('theme.css defines ONE .sw-trigger rule (quiet, existing tokens only) that both triggers opt into', () => {
    // Plain CWD-relative path, NOT `new URL('../src/theme.css', import.meta.url)`: Vite treats
    // that exact shape as its own asset-URL idiom and statically rewrites it to a dev-server URL
    // (`http://localhost:3000/...`) for `.css` specifically, which then makes `readFileSync`
    // throw ("The URL must be of scheme file") instead of returning the real file text — verified
    // empirically (a `.tsx` target under the same pattern, as `search.test.tsx`'s own
    // `readFileSync(new URL(rel, import.meta.url))` precedent uses, is untouched by that
    // transform; only `.css` is). `process.cwd()` is this project's `web/` root under `vitest run`.
    const css = readFileSync('src/theme.css', 'utf8')
    const selectorOccurrences = css.match(/\.sw-trigger\b/g) ?? []
    // The base rule, plus `.sw-trigger:hover,.sw-trigger:focus-visible{...}` repeating the class
    // for each pseudo-selector -- the SAME comma-separated idiom eyebrow.css's own
    // `.turn-speaker:hover, .turn-speaker:focus-visible` rule uses. Three occurrences, one
    // definition -- not a per-consumer copy.
    expect(selectorOccurrences.length).toBe(3)
    expect(css).toMatch(/\.sw-trigger\s*\{[^}]*border:\s*1px solid var\(--shore\)/)
    expect(css).toMatch(/\.sw-trigger\s*\{[^}]*border-radius:\s*999px/)
    expect(css).toMatch(/\.sw-trigger:hover[^{]*\{[^}]*background:\s*var\(--shore\)/)
  })
})

describe('CategoryFilter — dropdown open/close', () => {
  it('opens on trigger click: aria-expanded="true", panel present', async () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'View: chat' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')

    await userEvent.click(trigger)

    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(panel()).not.toBeNull()
  })

  it('re-clicking the trigger closes the panel', async () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'View: chat' })

    await userEvent.click(trigger)
    await userEvent.click(trigger)

    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('mousedown outside the panel closes it; mousedown inside the panel does not', async () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'View: chat' })

    await userEvent.click(trigger)
    const box = screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] })
    fireEvent.mouseDown(box)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')

    fireEvent.mouseDown(document.body)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
  })

  it('Escape closes the panel and returns focus to the trigger', async () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'View: chat' })

    await userEvent.click(trigger)
    const box = screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] })
    box.focus()
    fireEvent.keyDown(box, { key: 'Escape' })

    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)
  })
})

describe('CategoryFilter — presets (now INSIDE the panel, above the checkboxes)', () => {
  it('renders the three preset items above the five checkboxes, in that order', async () => {
    render(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    await openPanel()

    const scope = panel()
    const presetButtons = within(scope).getAllByRole('button')
    expect(presetButtons.map((b) => b.textContent)).toEqual(['chat', 'chat+harness', 'all'])
    const checkboxes = within(scope).getAllByRole('checkbox')
    expect(checkboxes).toHaveLength(5)

    // Document order: every preset button precedes every checkbox.
    const lastPreset = presetButtons[presetButtons.length - 1]
    const firstCheckbox = checkboxes[0]
    expect(
      lastPreset.compareDocumentPosition(firstCheckbox) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('highlights the item matching the current selection exactly', async () => {
    render(<CategoryFilter selection={PRESET_SETS['chat-harness']} setSelection={vi.fn()} />)
    await openPanel()
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'all' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('no item is highlighted for a custom combination', async () => {
    render(
      <CategoryFilter selection={new Set(['you-chat', 'tool-traffic'])} setSelection={vi.fn()} />,
    )
    await openPanel()
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'false',
    )
    expect(screen.getByRole('button', { name: 'all' }).getAttribute('aria-pressed')).toBe('false')
  })

  it("clicking a preset item sets the selection to that preset's set and KEEPS THE PANEL OPEN", async () => {
    const setSelection = vi.fn()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)
    await openPanel()

    await userEvent.click(screen.getByRole('button', { name: 'all' }))

    expect(setSelection).toHaveBeenCalledWith(PRESET_SETS.all)
    // The owner's explicit spec: the panel stays open on a preset click so the user can keep
    // adjusting -- the trigger is still expanded and the checkboxes are still reachable.
    expect(screen.getByRole('button', { name: /^View: /}).getAttribute('aria-expanded')).toBe(
      'true',
    )
    expect(screen.getAllByRole('checkbox')).toHaveLength(5)
  })

  // The owner's core complaint (carried over from the original T17 landing): a box toggle inside
  // the panel must be reflected on the preset item IMMEDIATELY (same render), not just on mount.
  it('a box toggle away from a preset drops its highlight LIVE; restoring the set brings it back', async () => {
    const { rerender } = render(
      <CategoryFilter selection={PRESET_SETS['chat-harness']} setSelection={vi.fn()} />,
    )
    await openPanel()
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'true',
    )

    const dropped = new Set(PRESET_SETS['chat-harness'])
    dropped.delete('harness-system')
    rerender(<CategoryFilter selection={dropped} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'false',
    )

    rerender(<CategoryFilter selection={PRESET_SETS['chat-harness']} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
  })
})

describe('CategoryFilter — checkboxes (inside the panel)', () => {
  it('opens with one checkbox per category, checked per the selection', async () => {
    render(
      <CategoryFilter
        selection={new Set(['you-chat', 'claude-thinking'])}
        setSelection={vi.fn()}
      />,
    )
    await openPanel()

    for (const [slug, label] of Object.entries(CATEGORY_LABELS) as Array<[CategorySlug, string]>) {
      const box = screen.getByRole('checkbox', { name: label })
      expect((box as HTMLInputElement).checked).toBe(
        slug === 'you-chat' || slug === 'claude-thinking',
      )
    }
  })

  it('checking a box adds its category to the selection', async () => {
    const setSelection = vi.fn()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)
    await openPanel()

    await userEvent.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['claude-chat'] }))

    expect(setSelection).toHaveBeenCalledWith(new Set(['you-chat', 'claude-chat']))
  })

  it('unchecking a box removes its category from the selection', async () => {
    const setSelection = vi.fn()
    render(
      <CategoryFilter
        selection={new Set(['you-chat', 'claude-chat'])}
        setSelection={setSelection}
      />,
    )
    await openPanel()

    await userEvent.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] }))

    expect(setSelection).toHaveBeenCalledWith(new Set(['claude-chat']))
  })

  it('prevents unchecking the last checked box', async () => {
    const setSelection = vi.fn()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)
    await openPanel()

    await userEvent.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] }))

    expect(setSelection).not.toHaveBeenCalled()
  })
})
