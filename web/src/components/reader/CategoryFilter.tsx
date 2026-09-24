import type { CSSProperties } from 'react'
import {
  ALL_CATEGORIES,
  CATEGORY_LABELS,
  PRESET_SETS,
  presetForSelection,
  type CategorySlug,
} from '../../lib/viewMode'

// Task T10: the reader header's replacement for the retired three-state ViewToggle (still used,
// unchanged, by RawRecordInspector's own independent in-modal filter — see that component and the
// T10 write-up for why it's out of scope here). Five checkboxes, one per category, plus the three
// presets as quick chips. Presentational only — state is OWNED by the page (useCategorySelection)
// and passed in, same "one owner per reader page" contract ViewToggle followed (plan critique F4).

const WRAP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  gap: 10,
}

const CHECKBOX_GROUP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 10,
  border: '1px solid var(--shore)',
  borderRadius: 6,
  padding: '2px 8px',
}

const CHECKBOX_LABEL_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  color: 'var(--mist)',
  cursor: 'pointer',
}

const CHIP_GROUP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
}

const CHIP_BASE_STYLE: CSSProperties = {
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
}

const CHIP_ACTIVE_STYLE: CSSProperties = {
  ...CHIP_BASE_STYLE,
  color: 'var(--dragonfly)',
  borderColor: 'var(--dragonfly)',
}

const PRESET_CHIPS: ReadonlyArray<{ preset: 'chat' | 'chat-harness' | 'all'; label: string }> = [
  { preset: 'chat', label: 'chat' },
  { preset: 'chat-harness', label: 'chat+harness' },
  { preset: 'all', label: 'all' },
]

export interface CategoryFilterProps {
  selection: ReadonlySet<CategorySlug>
  setSelection: (selection: ReadonlySet<CategorySlug>) => void
}

export function CategoryFilter({ selection, setSelection }: CategoryFilterProps) {
  const activePreset = presetForSelection(selection)

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
      <span role="group" aria-label="message categories" style={CHECKBOX_GROUP_STYLE}>
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
      <span role="group" aria-label="presets" style={CHIP_GROUP_STYLE}>
        {PRESET_CHIPS.map(({ preset, label }) => {
          const active = activePreset === preset
          return (
            <button
              key={preset}
              type="button"
              aria-pressed={active}
              onClick={() => setSelection(PRESET_SETS[preset])}
              style={active ? CHIP_ACTIVE_STYLE : CHIP_BASE_STYLE}
            >
              {label}
            </button>
          )
        })}
      </span>
    </span>
  )
}
