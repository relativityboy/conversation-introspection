import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { SessionSummary } from '../src/api/types'
import { SessionListItem } from '../src/components/SessionListItem'

const BASE: SessionSummary = {
  session_uuid: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  project_slug: '-Users-x-proj',
  ai_title: 'Charting the estuary',
  custom_title: null,
  user_title: null,
  started_at: '2026-07-19T10:00:00Z',
  last_activity_at: '2026-07-19T10:30:00Z',
  message_count: 4,
  favorite: false,
  match_snippet: null,
  match_record_uuid: null,
  match_agent_hex_id: null,
}

function withMatch(overrides: Partial<SessionSummary>): SessionSummary {
  return { ...BASE, ...overrides }
}

function renderItem(session: SessionSummary, search = '') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SessionListItem session={session} search={search} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SessionListItem — title link', () => {
  it('links the title to the session top /s/{uuid}', () => {
    renderItem(BASE)
    const titleLink = screen.getByRole('link', { name: /Charting the estuary/ })
    expect(titleLink.getAttribute('href')).toBe(`/s/${BASE.session_uuid}`)
  })

  it('carries the ?projects= search param onto the title link', () => {
    renderItem(BASE, '?projects=alpha')
    const titleLink = screen.getByRole('link', { name: /Charting the estuary/ })
    expect(titleLink.getAttribute('href')).toBe(`/s/${BASE.session_uuid}?projects=alpha`)
  })
})

describe('SessionListItem — no snippet (whole-row navigates to top)', () => {
  it('renders no snippet hint and every link points at the session top', () => {
    const { container } = renderItem(BASE, '?projects=alpha')
    expect(container.querySelector('.convo-snippet-hint')).toBeNull()
    // Every navigational anchor in the row (title + meta) lands on the session top, unchanged.
    for (const a of container.querySelectorAll('a')) {
      expect(a.getAttribute('href')).toBe(`/s/${BASE.session_uuid}?projects=alpha`)
    }
  })
})

describe('SessionListItem — snippet click deep-links to the matched message', () => {
  it('links a main-transcript snippet to /s/{uuid}/m/{record}', () => {
    const session = withMatch({
      match_snippet: 'a <mark>tidal</mark> wave',
      match_record_uuid: 'rec-42',
      match_agent_hex_id: null,
    })
    const { container } = renderItem(session, '?projects=alpha')
    const snippet = container.querySelector('.convo-snippet-hint') as HTMLAnchorElement
    expect(snippet).not.toBeNull()
    expect(snippet.tagName).toBe('A')
    expect(snippet.getAttribute('href')).toBe(`/s/${session.session_uuid}/m/rec-42?projects=alpha`)
  })

  it('links a subagent-transcript snippet through the /a/{hex}/ drill-in', () => {
    const session = withMatch({
      match_snippet: 'a lone <mark>cormorant</mark>',
      match_record_uuid: 'rec-sub',
      match_agent_hex_id: 'abc123',
    })
    const { container } = renderItem(session)
    const snippet = container.querySelector('.convo-snippet-hint') as HTMLAnchorElement
    expect(snippet.getAttribute('href')).toBe(
      `/s/${session.session_uuid}/a/abc123/m/rec-sub`,
    )
  })

  it('keeps the title link on the session top even when a snippet is present', () => {
    const session = withMatch({
      match_snippet: 'a <mark>tidal</mark> wave',
      match_record_uuid: 'rec-42',
    })
    renderItem(session)
    const titleLink = screen.getByRole('link', { name: /Charting the estuary/ })
    expect(titleLink.getAttribute('href')).toBe(`/s/${session.session_uuid}`)
  })
})

describe('SessionListItem — HTML validity', () => {
  it('never nests one anchor inside another (invalid interactive content)', () => {
    const session = withMatch({
      match_snippet: 'a <mark>tidal</mark> wave',
      match_record_uuid: 'rec-42',
    })
    const { container } = renderItem(session)
    for (const anchor of container.querySelectorAll('a')) {
      expect(anchor.querySelector('a')).toBeNull()
    }
  })
})
