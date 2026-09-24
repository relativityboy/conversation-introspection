import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CategoryFilter } from '../src/components/reader/CategoryFilter'
import { CATEGORY_LABELS, PRESET_SETS, type CategorySlug } from '../src/lib/viewMode'

describe('CategoryFilter', () => {
  it('renders one checkbox per category, labelled and checked per the selection', () => {
    render(
      <CategoryFilter selection={new Set(['you-chat', 'claude-thinking'])} setSelection={vi.fn()} />,
    )
    for (const [slug, label] of Object.entries(CATEGORY_LABELS) as Array<[CategorySlug, string]>) {
      const box = screen.getByRole('checkbox', { name: label })
      expect(box).toBeDefined()
      expect((box as HTMLInputElement).checked).toBe(
        slug === 'you-chat' || slug === 'claude-thinking',
      )
    }
  })

  it('checking a box adds its category to the selection', async () => {
    const setSelection = vi.fn()
    const user = userEvent.setup()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)

    await user.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['claude-chat'] }))

    expect(setSelection).toHaveBeenCalledWith(new Set(['you-chat', 'claude-chat']))
  })

  it('unchecking a box removes its category from the selection', async () => {
    const setSelection = vi.fn()
    const user = userEvent.setup()
    render(
      <CategoryFilter selection={new Set(['you-chat', 'claude-chat'])} setSelection={setSelection} />,
    )

    await user.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] }))

    expect(setSelection).toHaveBeenCalledWith(new Set(['claude-chat']))
  })

  it('prevents unchecking the last checked box', async () => {
    const setSelection = vi.fn()
    const user = userEvent.setup()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)

    await user.click(screen.getByRole('checkbox', { name: CATEGORY_LABELS['you-chat'] }))

    expect(setSelection).not.toHaveBeenCalled()
  })

  it('highlights the chip matching the current selection exactly', () => {
    render(<CategoryFilter selection={PRESET_SETS['chat-harness']} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'all' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('no chip is highlighted for a custom combination', () => {
    render(
      <CategoryFilter
        selection={new Set(['you-chat', 'tool-traffic'])}
        setSelection={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'false',
    )
    expect(screen.getByRole('button', { name: 'all' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('clicking a preset chip sets the selection to that preset\'s set', async () => {
    const setSelection = vi.fn()
    const user = userEvent.setup()
    render(<CategoryFilter selection={new Set(['you-chat'])} setSelection={setSelection} />)

    await user.click(screen.getByRole('button', { name: 'all' }))

    expect(setSelection).toHaveBeenCalledWith(PRESET_SETS.all)
  })

  it('checking boxes to exactly a preset set is reflected by the chip on the next render', () => {
    const { rerender } = render(
      <CategoryFilter selection={new Set(['you-chat'])} setSelection={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')

    rerender(<CategoryFilter selection={PRESET_SETS.chat} setSelection={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('true')
  })
})
