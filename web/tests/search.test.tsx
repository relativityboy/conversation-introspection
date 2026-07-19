import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  GlobalSearchResult,
  HitOut,
  SessionSearchResult,
  SessionSummary,
} from '../src/api/types'
import { ConversationSearch, ConversationSearchResults } from '../src/components/search/ConversationSearch'
import { HitSnippet } from '../src/components/search/HitSnippet'
import { TabBar } from '../src/components/TabBar'
import { SearchPage } from '../src/routes/SearchPage'

// Mock the api client module (not global fetch) — hooks.ts imports fetchSearch directly, so this
// swaps the network layer for every useSearch in one place. Same convention as Sidebar.test.tsx.
const { fetchSearch } = vi.hoisted(() => ({ fetchSearch: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchSearch }
})

beforeEach(() => {
  fetchSearch.mockReset()
})

// --- fixtures ---------------------------------------------------------------------------------

function makeSession(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_uuid: 'uuid-1',
    project_slug: '-Users-x-proj',
    ai_title: 'My Session',
    custom_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 3,
    favorite: false,
    ...over,
  }
}

function makeHit(over: Partial<HitOut> = {}): HitOut {
  return {
    record_uuid: 'rec-1',
    transcript_id: 7,
    block_index: 2,
    block_kind: 'text',
    snippet: 'a <mark>hit</mark> b',
    timestamp: null,
    ...over,
  }
}

function globalResult(over: Partial<GlobalSearchResult> = {}): GlobalSearchResult {
  return {
    groups: [{ session: makeSession(), hits: [makeHit()], has_more: false }],
    total: 1,
    ...over,
  }
}

// --- harness ----------------------------------------------------------------------------------

function setup(ui: ReactNode, path = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const locationRef: { current: { pathname: string; search: string } | null } = { current: null }

  function LocationProbe() {
    const location = useLocation()
    locationRef.current = { pathname: location.pathname, search: location.search }
    return null
  }

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        {ui}
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { locationRef, ...utils }
}

// --- SearchPage: URL sync + empty state -------------------------------------------------------

describe('SearchPage', () => {
  it('does not call the API and shows a calm empty state when q is absent', async () => {
    setup(<SearchPage />, '/search')

    expect(screen.getByText('Search every archived conversation')).toBeDefined()
    // useSearch gates on a non-empty q, so no round-trip fires. Give any (wrong) async call a beat.
    await Promise.resolve()
    expect(fetchSearch).not.toHaveBeenCalled()
  })

  it('commits the query to ?q= on Enter and searches with global scope', async () => {
    fetchSearch.mockResolvedValue(globalResult())
    const user = userEvent.setup()
    const { locationRef } = setup(<SearchPage />, '/search')

    await user.type(screen.getByRole('searchbox', { name: 'Search all conversations' }), 'hello{Enter}')

    await waitFor(() => expect(locationRef.current?.search).toBe('?q=hello'))
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledWith('hello', 'global', undefined))
  })

  it('renders grouped results — a serif session header link and hit snippets — with a total line', async () => {
    fetchSearch.mockResolvedValue(globalResult())
    setup(<SearchPage />, '/search?q=foo')

    const header = await screen.findByRole('link', { name: 'My Session' })
    expect(header.getAttribute('href')).toBe('/s/uuid-1')
    expect(screen.getByText('1 match')).toBeDefined()
    // The hit snippet's <mark> segment renders as an element.
    expect(document.querySelector('mark')?.textContent).toBe('hit')
  })

  it('links a capped group to the in-conversation search view via has_more', async () => {
    fetchSearch.mockResolvedValue(
      globalResult({ groups: [{ session: makeSession(), hits: [makeHit()], has_more: true }] }),
    )
    setup(<SearchPage />, '/search?q=foo')

    const more = await screen.findByRole('link', { name: 'more in this conversation →' })
    expect(more.getAttribute('href')).toBe('/s/uuid-1?q=foo')
  })
})

// --- HitSnippet: mark-splitting is sanitized, links deep --------------------------------------

describe('HitSnippet', () => {
  it('renders <mark> as an element and leaves other angle-bracket content as inert text', () => {
    const hit = makeHit({ snippet: 'a <mark>hit</mark> b <script>x</script>' })
    const { container } = setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/search?q=foo')

    // The matched term is a real <mark> element…
    expect(container.querySelector('mark')?.textContent).toBe('hit')
    // …but the raw <script> in the snippet is TEXT, never a DOM node (no HTML injection).
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>x</script>')
  })

  it('shows the block_kind badge and deep-links the hit, carrying q', () => {
    const hit = makeHit({ block_kind: 'thinking' })
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/')

    expect(screen.getByText('thinking')).toBeDefined()
    const link = screen.getByRole('link')
    expect(link.getAttribute('href')).toBe('/s/uuid-1/m/rec-1?q=foo')
  })

  it('degrades to the session route when the hit has no record_uuid', () => {
    const hit = makeHit({ record_uuid: null })
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/')

    expect(screen.getByRole('link').getAttribute('href')).toBe('/s/uuid-1?q=foo')
  })
})

// --- ConversationSearch: header input + scoped results ----------------------------------------

describe('ConversationSearch', () => {
  it('commits to /s/{uuid}?q=term on Enter, dropping any deep-link segment', async () => {
    const user = userEvent.setup()
    const { locationRef } = setup(
      <ConversationSearch sessionUuid="uuid-1" />,
      '/s/uuid-1/m/rec-9',
    )

    await user.type(screen.getByRole('searchbox', { name: 'Search this conversation' }), 'needle{Enter}')

    await waitFor(() => expect(locationRef.current?.pathname).toBe('/s/uuid-1'))
    expect(locationRef.current?.search).toBe('?q=needle')
  })
})

describe('ConversationSearchResults', () => {
  it('renders flat session-scoped hits and a back-link that clears q (replace)', async () => {
    fetchSearch.mockResolvedValue({ items: [makeHit()], total: 1 } satisfies SessionSearchResult)
    const user = userEvent.setup()
    const { locationRef } = setup(
      <ConversationSearchResults sessionUuid="uuid-1" q="foo" />,
      '/s/uuid-1?q=foo',
    )

    await waitFor(() => expect(fetchSearch).toHaveBeenCalledWith('foo', 'session', 'uuid-1'))
    expect(await screen.findByText('1 match')).toBeDefined()

    await user.click(screen.getByRole('button', { name: '← back to conversation' }))

    await waitFor(() => expect(locationRef.current?.search).toBe(''))
    expect(locationRef.current?.pathname).toBe('/s/uuid-1')
  })
})

// --- TabBar: active state follows the route ---------------------------------------------------

describe('TabBar', () => {
  it('selects the search tab on /search and disables the conversation tab', () => {
    setup(<TabBar />, '/search')

    expect(screen.getByRole('tab', { name: 'Search all conversations' }).getAttribute('aria-selected')).toBe('true')
    const convo = screen.getByRole('tab', { name: 'Current conversation' })
    expect(convo.getAttribute('aria-selected')).toBe('false')
    expect(convo.getAttribute('aria-disabled')).toBe('true')
    expect(convo.getAttribute('href')).toBeNull()
  })

  it('selects the conversation tab on a session route and links it to the base session', () => {
    setup(<TabBar />, '/s/uuid-1/m/rec-2')

    const convo = screen.getByRole('tab', { name: 'Current conversation' })
    expect(convo.getAttribute('aria-selected')).toBe('true')
    expect(convo.getAttribute('href')).toBe('/s/uuid-1')
    expect(screen.getByRole('tab', { name: 'Search all conversations' }).getAttribute('aria-selected')).toBe('false')
  })
})

// --- guardrail: no raw-HTML injection sink anywhere in the search surface ----------------------

describe('no raw-HTML sink', () => {
  it('never uses dangerouslySetInnerHTML in any search component', () => {
    const files = [
      '../src/components/search/HitSnippet.tsx',
      '../src/components/search/GlobalSearchTab.tsx',
      '../src/components/search/ConversationSearch.tsx',
      '../src/routes/SearchPage.tsx',
    ]
    for (const rel of files) {
      const source = readFileSync(new URL(rel, import.meta.url), 'utf8')
      expect(source).not.toContain('dangerouslySetInnerHTML')
    }
  })
})
