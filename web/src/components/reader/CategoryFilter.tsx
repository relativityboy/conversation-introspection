import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import {
  ALL_CATEGORIES,
  CATEGORY_LABELS,
  PRESET_SETS,
  presetForSelection,
  type CategorySlug,
} from '../../lib/viewMode'

// Task T17 (owner ruling 2026-09-25), item-2 revision: the header shrinks to the dropdown
// trigger ALONE — the three preset buttons move INSIDE the panel, as items above the five
// checkboxes, keeping their T17 configure-and-reflect behavior (highlight iff exact match, live —
// read off the SAME `selection` prop the checkboxes toggle, so a box change is reflected on the
// very next render). Item-3 revision (same day, owner spec via coordinator, after a live-room
// look): restores a decorative ▾ arrow (an aria-hidden sibling, excluded from the accessible
// name), reserves MORE trigger width with explicit headroom (the item-2 ch-only formula
// undershot in the real room — jsdom can't catch that, the owner's eyes are the instrument), and
// gives the trigger a real quiet button appearance via the shared `.sw-trigger` class
// (theme.css) — the SAME class ActionsMenu.tsx's `actions ▾` trigger now carries, so the two
// can't drift apart. The dropdown mechanism itself (click-away via document mousedown +
// containment, an element-scoped Escape, focus returned to the trigger on close) is UNCHANGED
// from the original T17 landing — still mirrors ActionsMenu.tsx's OWN idiom, not
// ProjectFilterBar's blur-based one, for the same reason: a checkbox/preset-item click inside the
// panel must not blur/close the trigger. Presentational only — state is OWNED by the page
// (useCategorySelection) and passed in, same "one owner per reader page" contract ViewToggle/
// T10's control followed (plan critique F4).

const WRAP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
}

// Item-3 revision: the border/radius/background/color/cursor/hover treatment moved to the shared
// `.sw-trigger` class (theme.css) — ActionsMenu's trigger carries the SAME class now, one
// definition, so they can't drift. Only this component's own LAYOUT stays inline (font sizing
// inherited from the mono `WRAP_STYLE` ancestor, padding, the reserved width, left-alignment so
// the fixed-width padding doesn't center-float a shorter label inside the box) — mirrors
// `.sw-input`'s own shared-contrast/per-consumer-layout split (theme.css).
const TRIGGER_STYLE: CSSProperties = {
  fontFamily: 'inherit',
  fontSize: 'inherit',
  letterSpacing: 'inherit',
  lineHeight: 'inherit',
  padding: '2px 8px',
  textAlign: 'left',
}

// Mirrors ActionsMenu's PANEL_STYLE (same floating-panel vocabulary as the project filter's combo
// popup) — right-aligned rather than left, since this trigger sits at the right edge of the
// session-meta row and a left-aligned panel would run off-screen.
const PANEL_STYLE: CSSProperties = {
  position: 'absolute',
  top: 'calc(100% + 4px)',
  right: 0,
  zIndex: 20,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'stretch',
  gap: 6,
  padding: '8px 10px',
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  boxShadow: '0 6px 20px rgba(0,0,0,.35)',
  whiteSpace: 'nowrap',
}

const PRESET_GROUP_STYLE: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'stretch',
  gap: 4,
}

// A quiet hairline between the presets and the checkboxes — the only visual separator this panel
// needs; existing tokens only (var(--shore)), no new colors, no heavy borders.
const DIVIDER_STYLE: CSSProperties = {
  height: 0,
  borderTop: '1px solid var(--shore)',
  margin: '2px 0',
}

const PRESET_ITEM_BASE_STYLE: CSSProperties = {
  fontFamily: 'inherit',
  fontSize: 'inherit',
  letterSpacing: 'inherit',
  lineHeight: 'inherit',
  background: 'none',
  border: '1px solid var(--shore)',
  borderRadius: 999,
  padding: '2px 8px',
  cursor: 'pointer',
  color: 'var(--mist)',
  textAlign: 'left',
}

const PRESET_ITEM_ACTIVE_STYLE: CSSProperties = {
  ...PRESET_ITEM_BASE_STYLE,
  color: 'var(--dragonfly)',
  borderColor: 'var(--dragonfly)',
}

const CHECKBOX_LABEL_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  color: 'var(--mist)',
  cursor: 'pointer',
}

const PRESET_ITEMS: ReadonlyArray<{ preset: 'chat' | 'chat-harness' | 'all'; label: string }> = [
  { preset: 'chat', label: 'chat' },
  { preset: 'chat-harness', label: 'chat+harness' },
  { preset: 'all', label: 'all' },
]

const PANEL_ID = 'category-filter-panel'

// The trigger's collapsed label is exactly `View: <label>` (owner spec, item-2 revision) — the
// matching preset's own short name when the selection equals one exactly (the SAME check the
// preset items' own highlight uses), otherwise a compact honest count, `x / y`, rather than
// naming an arbitrary combination.
const PRESET_TRIGGER_LABELS: Record<'chat' | 'chat-harness' | 'all', string> = {
  chat: 'View: chat',
  'chat-harness': 'View: chat+harness',
  all: 'View: all',
}

function customTriggerLabel(selection: ReadonlySet<CategorySlug>): string {
  return `View: ${selection.size} / ${ALL_CATEGORIES.length}`
}

function triggerLabel(selection: ReadonlySet<CategorySlug>): string {
  const preset = presetForSelection(selection)
  return preset !== null ? PRESET_TRIGGER_LABELS[preset] : customTriggerLabel(selection)
}

// The decorative arrow — an aria-hidden SIBLING of the label text, not appended to it: accessible-
// name computation (the same one `getByRole(..., {name})` uses) excludes `aria-hidden` content, so
// the trigger's accessible name stays the exact `View: <label>` string the owner specified while
// the arrow still shows visually. Restored item-3 revision — the original T17 landing folded the
// arrow into the same text node the label used, which the item-2 revision's literal reading of
// "the label format becomes exactly `View: <label>`" then dropped entirely; this restores it
// losslessly instead of re-adding it to the accessible name.
const ARROW_TEXT = ' ▾'

// Constant trigger width ("no layout shift across any label transition"), reserved to the WIDEST
// of the four possible label shapes. Item-3 revision: the item-2 landing's plain
// `LONGEST_LABEL.length` ch-count undershot in the real room (a `ch` unit is defined off the
// font's "0" glyph, which isn't exactly every OTHER character's width even in a nominally
// monospace font, and it didn't budget for the arrow or the button's own padding at all) — jsdom
// can't catch that, so per the owner's explicit call ("favor generous over clever"), this now
// reserves the longest label's own characters PLUS the arrow's characters PLUS a generous
// ch-equivalent for the button's horizontal padding PLUS explicit headroom, rather than
// re-deriving a tighter "correct" number that risks undershooting again for the same reason the
// first one did. `customTriggerLabel`'s own width is constant across every reachable custom
// selection too: `ALL_CATEGORIES.length` is a fixed single digit (5) and a "custom" (non-preset)
// selection size is always 1–4 — also always a single digit — so every `x / y` shape is the same
// length regardless of which combination produced it.
const TRIGGER_PADDING_CH = 2
const TRIGGER_HEADROOM_CH = 2
const TRIGGER_WIDTH_CH =
  Math.max(
    ...Object.values(PRESET_TRIGGER_LABELS).map((label) => label.length),
    customTriggerLabel(new Set(['you-chat'])).length,
  ) +
  ARROW_TEXT.length +
  TRIGGER_PADDING_CH +
  TRIGGER_HEADROOM_CH

export interface CategoryFilterProps {
  selection: ReadonlySet<CategorySlug>
  setSelection: (selection: ReadonlySet<CategorySlug>) => void
}

export function CategoryFilter({ selection, setSelection }: CategoryFilterProps) {
  const activePreset = presetForSelection(selection)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  // Click-outside via document mousedown + containment (ActionsMenu's pattern) — listener exists
  // only while open, removed on close/unmount.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  function toggle(slug: CategorySlug) {
    const checked = selection.has(slug)
    // At least one box always checked — matches the server's 422-on-empty stance. Unchecking the
    // LAST checked box is a no-op rather than an error: simplest honest floor for a control with
    // no submit step of its own.
    if (checked && selection.size === 1) return
    const next = new Set(selection)
    if (checked) next.delete(slug)
    else next.add(slug)
    setSelection(next)
  }

  return (
    <span className="category-filter mono" style={WRAP_STYLE}>
      <span
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
          className="sw-trigger"
          aria-expanded={open}
          aria-controls={PANEL_ID}
          onClick={() => (open ? close() : setOpen(true))}
          style={{ ...TRIGGER_STYLE, width: `${TRIGGER_WIDTH_CH}ch` }}
        >
          {triggerLabel(selection)}
          <span aria-hidden="true">{ARROW_TEXT}</span>
        </button>
        {open && (
          <span id={PANEL_ID} role="group" aria-label="message categories" style={PANEL_STYLE}>
            <span role="group" aria-label="presets" style={PRESET_GROUP_STYLE}>
              {PRESET_ITEMS.map(({ preset, label }) => {
                const active = activePreset === preset
                return (
                  <button
                    key={preset}
                    type="button"
                    aria-pressed={active}
                    // Owner spec: a preset click configures the boxes but leaves the panel OPEN
                    // — the user may keep adjusting — so this never touches `open`/`close`.
                    onClick={() => setSelection(PRESET_SETS[preset])}
                    style={active ? PRESET_ITEM_ACTIVE_STYLE : PRESET_ITEM_BASE_STYLE}
                  >
                    {label}
                  </button>
                )
              })}
            </span>
            <span style={DIVIDER_STYLE} aria-hidden="true" />
            {ALL_CATEGORIES.map((slug) => (
              <label key={slug} style={CHECKBOX_LABEL_STYLE}>
                <input
                  type="checkbox"
                  checked={selection.has(slug)}
                  onChange={() => toggle(slug)}
                />
                {CATEGORY_LABELS[slug]}
              </label>
            ))}
          </span>
        )}
      </span>
    </span>
  )
}
