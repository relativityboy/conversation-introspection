import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ViewToggle } from '../src/components/reader/ViewToggle'

describe('ViewToggle', () => {
  it('renders three segments labeled chat / chat+harness / all', () => {
    render(<ViewToggle view="chat" setView={() => {}} />)
    expect(screen.getByRole('button', { name: 'chat' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'chat+harness' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'all' })).toBeDefined()
  })

  it('marks only the current view as pressed', () => {
    render(<ViewToggle view="chat-harness" setView={() => {}} />)
    expect(screen.getByRole('button', { name: 'chat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'chat+harness' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    expect(screen.getByRole('button', { name: 'all' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('clicking a segment calls setView with that segment’s mode, even from an inactive segment', async () => {
    const setView = vi.fn()
    render(<ViewToggle view="chat" setView={setView} />)

    await userEvent.click(screen.getByRole('button', { name: 'all' }))
    expect(setView).toHaveBeenCalledWith('all')

    await userEvent.click(screen.getByRole('button', { name: 'chat+harness' }))
    expect(setView).toHaveBeenCalledWith('chat-harness')
  })
})
