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
    user_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 3,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
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
    agent_hex_id: null,
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
    // fetchSearch's full positional signature (q, scope, session, limit, offset, projects) — no
    // ?projects= in the URL here, so the trailing arg is undefined (Task 9: useSearch threads it
    // through as a real positional arg, not a re-parsed string, for every call).
    await waitFor(() =>
      expect(fetchSearch).toHaveBeenCalledWith(
        'hello',
        'global',
        undefined,
        undefined,
        undefined,
        undefined,
      ),
    )
  })

  // Task 9: the global search call must include the CURRENT ?projects= — read at fire time (the
  // same render that reads ?q=), not a value captured once at mount.
  it('includes the current ?projects= in the global search request', async () => {
    fetchSearch.mockResolvedValue(globalResult())
    setup(<SearchPage />, '/search?q=foo&projects=alpha,mid')

    await waitFor(() =>
      expect(fetchSearch).toHaveBeenCalledWith(
        'foo',
        'global',
        undefined,
        undefined,
        undefined,
        ['alpha', 'mid'],
      ),
    )
  })

  // Task 9: the q-commit path builds its next URLSearchParams from `prev` (the CURRENT params),
  // so ?projects= should survive a fresh Enter-commit without any special-casing.
  it('preserves ?projects= across the q-commit (Enter) round trip', async () => {
    fetchSearch.mockResolvedValue(globalResult())
    const user = userEvent.setup()
    const { locationRef } = setup(<SearchPage />, '/search?projects=alpha')

    await user.type(screen.getByRole('searchbox', { name: 'Search all conversations' }), 'hello{Enter}')

    await waitFor(() => {
      const params = new URLSearchParams(locationRef.current?.search)
      expect(params.get('q')).toBe('hello')
      expect(params.get('projects')).toBe('alpha')
    })
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

  // --- title precedence (§14.3 binding, enforced identically at every render site):
  // user_title > ai_title > custom_title > uuid-prefix -------------------------------------

  it('shows user_title over ai_title in the group header when the session has been renamed', async () => {
    fetchSearch.mockResolvedValue(
      globalResult({
        groups: [
          { session: makeSession({ user_title: 'Renamed' }), hits: [makeHit()], has_more: false },
        ],
      }),
    )
    setup(<SearchPage />, '/search?q=foo')

    expect(await screen.findByRole('link', { name: 'Renamed' })).toBeDefined()
    expect(screen.queryByText('My Session')).toBeNull()
  })

  it('falls through to the uuid-prefix in the group header when no title of any kind is set', async () => {
    const session = makeSession({ ai_title: null, custom_title: null, user_title: null })
    fetchSearch.mockResolvedValue(
      globalResult({ groups: [{ session, hits: [makeHit()], has_more: false }] }),
    )
    setup(<SearchPage />, '/search?q=foo')

    expect(await screen.findByRole('link', { name: session.session_uuid.slice(0, 8) })).toBeDefined()
  })

  it('links a capped group to the in-conversation search view via has_more', async () => {
    fetchSearch.mockResolvedValue(
      globalResult({ groups: [{ session: makeSession(), hits: [makeHit()], has_more: true }] }),
    )
    setup(<SearchPage />, '/search?q=foo')

    const more = await screen.findByRole('link', { name: 'more in this conversation →' })
    expect(more.getAttribute('href')).toBe('/s/uuid-1?q=foo')
  })

  // --- Task 9: group-header + "more" links carry ?projects= ----------------------------------

  it('carries ?projects= on the group header link (which otherwise has no search at all)', async () => {
    fetchSearch.mockResolvedValue(globalResult())
    setup(<SearchPage />, '/search?q=foo&projects=alpha,mid')

    const header = await screen.findByRole('link', { name: 'My Session' })
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(header.getAttribute('href')).toBe('/s/uuid-1?projects=alpha%2Cmid')
  })

  it('carries ?projects= alongside ?q= on the "more in this conversation" link', async () => {
    fetchSearch.mockResolvedValue(
      globalResult({ groups: [{ session: makeSession(), hits: [makeHit()], has_more: true }] }),
    )
    setup(<SearchPage />, '/search?q=foo&projects=alpha,mid')

    const more = await screen.findByRole('link', { name: 'more in this conversation →' })
    expect(more.getAttribute('href')).toBe('/s/uuid-1?q=foo&projects=alpha%2Cmid')
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

  it('deep-links a subagent hit through the /a/{hex}/ drill-in, not the main path', () => {
    const hit = makeHit({ agent_hex_id: 'deadbeef' })
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/')

    expect(screen.getByRole('link').getAttribute('href')).toBe(
      '/s/uuid-1/a/deadbeef/m/rec-1?q=foo',
    )
  })

  it('degrades a record-less subagent hit to the subagent base path', () => {
    const hit = makeHit({ agent_hex_id: 'deadbeef', record_uuid: null })
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/')

    expect(screen.getByRole('link').getAttribute('href')).toBe('/s/uuid-1/a/deadbeef?q=foo')
  })

  // --- Task 9: `projects` prop is appended onto the deep link (q always wins the position) ------

  it('carries a `projects` prop onto the deep link alongside q', () => {
    const hit = makeHit()
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" projects={['alpha', 'mid']} />, '/')

    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(screen.getByRole('link').getAttribute('href')).toBe(
      '/s/uuid-1/m/rec-1?q=foo&projects=alpha%2Cmid',
    )
  })

  it('omits projects from the link when the prop is absent (unchanged from before Task 9)', () => {
    const hit = makeHit()
    setup(<HitSnippet sessionUuid="uuid-1" hit={hit} q="foo" />, '/')

    expect(screen.getByRole('link').getAttribute('href')).toBe('/s/uuid-1/m/rec-1?q=foo')
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

  // Task 9: the commit path builds its next URLSearchParams from the CURRENT `searchParams` (not
  // from scratch), so ?projects= should ride along with no code change needed here — this test
  // proves that contract holds.
  it('preserves ?projects= across the q-commit round trip', async () => {
    const user = userEvent.setup()
    const { locationRef } = setup(
      <ConversationSearch sessionUuid="uuid-1" />,
      '/s/uuid-1?projects=alpha',
    )

    await user.type(screen.getByRole('searchbox', { name: 'Search this conversation' }), 'needle{Enter}')

    await waitFor(() => {
      const params = new URLSearchParams(locationRef.current?.search)
      expect(params.get('q')).toBe('needle')
      expect(params.get('projects')).toBe('alpha')
    })
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

    // Full positional fetchSearch signature (Task 9 threads projects through as a real positional
    // arg on every useSearch call) — session scope's trailing arg is undefined here since no
    // ?projects= is present.
    await waitFor(() =>
      expect(fetchSearch).toHaveBeenCalledWith(
        'foo',
        'session',
        'uuid-1',
        undefined,
        undefined,
        undefined,
      ),
    )
    expect(await screen.findByText('1 match')).toBeDefined()

    await user.click(screen.getByRole('button', { name: '← back to conversation' }))

    await waitFor(() => expect(locationRef.current?.search).toBe(''))
    expect(locationRef.current?.pathname).toBe('/s/uuid-1')
  })

  // Task 9, named contract: session-scope search does NOT pass projects to the server (it would
  // be meaningless — you're already scoped to one session) even though ?projects= is present in
  // the URL. The client stays clean: no 6th positional arg reaches fetchSearch.
  it('does NOT pass projects to fetchSearch even when ?projects= is present in the URL', async () => {
    fetchSearch.mockResolvedValue({ items: [makeHit()], total: 1 } satisfies SessionSearchResult)
    setup(
      <ConversationSearchResults sessionUuid="uuid-1" q="foo" />,
      '/s/uuid-1?q=foo&projects=alpha,mid',
    )

    await waitFor(() =>
      expect(fetchSearch).toHaveBeenCalledWith(
        'foo',
        'session',
        'uuid-1',
        undefined,
        undefined,
        undefined,
      ),
    )
  })

  // Distinct from the query above: ?projects= is still a deep-link concern even in session scope
  // (it's app-level UI state, not a search filter) — the hit's own link must still carry it.
  it('still carries ?projects= on the rendered hit link (a link-preservation concern, not a query one)', async () => {
    fetchSearch.mockResolvedValue({ items: [makeHit()], total: 1 } satisfies SessionSearchResult)
    setup(
      <ConversationSearchResults sessionUuid="uuid-1" q="foo" />,
      '/s/uuid-1?q=foo&projects=alpha,mid',
    )

    const link = await screen.findByRole('link')
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(link.getAttribute('href')).toBe('/s/uuid-1/m/rec-1?q=foo&projects=alpha%2Cmid')
  })

  // The back-link's setSearchParams updater builds from `prev` (current params) too — projects
  // should survive clearing q.
  it('preserves ?projects= when the back-link clears q', async () => {
    fetchSearch.mockResolvedValue({ items: [makeHit()], total: 1 } satisfies SessionSearchResult)
    const user = userEvent.setup()
    const { locationRef } = setup(
      <ConversationSearchResults sessionUuid="uuid-1" q="foo" />,
      '/s/uuid-1?q=foo&projects=alpha',
    )
    await screen.findByText('1 match')

    await user.click(screen.getByRole('button', { name: '← back to conversation' }))

    await waitFor(() => expect(locationRef.current?.search).toBe('?projects=alpha'))
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

  // Task 9, §14.2 binding: "Both search tabs ... inherit the filter context."
  it('preserves ?projects= on the "Search all conversations" tab link', () => {
    setup(<TabBar />, '/s/uuid-1?projects=alpha,mid')

    const search = screen.getByRole('tab', { name: 'Search all conversations' })
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(search.getAttribute('href')).toBe('/search?projects=alpha%2Cmid')
  })

  it('preserves ?projects= on the "Current conversation" tab link', () => {
    setup(<TabBar />, '/s/uuid-1/m/rec-2?projects=alpha,mid')

    const convo = screen.getByRole('tab', { name: 'Current conversation' })
    // %2C: URLSearchParams.toString() percent-encodes commas on serialization (see
    // Sidebar.test.tsx for the full note; consistent across every writeProjects-built link).
    expect(convo.getAttribute('href')).toBe('/s/uuid-1?projects=alpha%2Cmid')
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
