import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { TabBar } from '../src/components/TabBar'

function setup(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TabBar />
    </MemoryRouter>,
  )
}

describe('TabBar', () => {
  it('renders three tabs', () => {
    setup('/search')

    expect(screen.getByRole('tab', { name: 'Search all conversations' })).toBeDefined()
    expect(screen.getByRole('tab', { name: 'Current conversation' })).toBeDefined()
    expect(screen.getByRole('tab', { name: 'Memories' })).toBeDefined()
  })

  it('selects the Memories tab on /memories and carries the current ?projects= onto its href', () => {
    setup('/memories?projects=alpha,mid')

    const memories = screen.getByRole('tab', { name: 'Memories' })
    expect(memories.getAttribute('aria-selected')).toBe('true')
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(memories.getAttribute('href')).toBe('/memories?projects=alpha%2Cmid')
  })

  it('leaves the first two tabs unchanged (search active at /search; conversation tab disabled with no session)', () => {
    setup('/search')

    expect(
      screen.getByRole('tab', { name: 'Search all conversations' }).getAttribute('aria-selected'),
    ).toBe('true')

    const memories = screen.getByRole('tab', { name: 'Memories' })
    expect(memories.getAttribute('aria-selected')).toBe('false')

    const convo = screen.getByRole('tab', { name: 'Current conversation' })
    expect(convo.getAttribute('aria-selected')).toBe('false')
    expect(convo.getAttribute('aria-disabled')).toBe('true')
    expect(convo.getAttribute('href')).toBeNull()
  })
})
