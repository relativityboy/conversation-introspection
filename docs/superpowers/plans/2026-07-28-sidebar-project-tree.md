# Sidebar Project Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A toggleable directory-style sidebar view (`<project>/<sessions>`) alongside the existing flat list, with the search box relocated to the topbar row.

**Architecture:** Client composition over existing endpoints (spec §3 option A — see `docs/superpowers/specs/2026-07-28-sidebar-project-tree-design.md`, the binding document for every judgment call here). Browse mode: `GET /projects` + lazy per-project `GET /sessions?projects=<slug>` on expand. Filtered mode: the flat view's single query grouped by `project_slug`, pruned + auto-expanded. Two server touches: archived-excluded `/projects` counts, `['projects']` cache invalidation.

**Tech Stack:** React 19 + react-router + @tanstack/react-query + vitest/jsdom (web); FastAPI + SQLAlchemy + pytest (server). No new dependencies.

## Global Constraints

- **Zero-legacy** (standing pre-release law): delete, don't deprecate. No compat aliases, no dead exports left behind.
- **Styling:** inline `CSSProperties` + semantic classNames only — no new stylesheets beyond the `App.css` grid-area change in Task 4 (repo convention, `Sidebar.tsx:11-12`).
- **Web tests** mock `../src/api/client` via `vi.hoisted` + `vi.mock` (pattern at `tests/Sidebar.test.tsx:15-27`), never global fetch. Run: `cd web && npx vitest run tests/<file>`.
- **Server tests:** `cd server && uv run pytest tests/test_api_sessions.py -q`.
- **Minion economics (relativityboy, 2026-07-28):** model choice per task optimizes for done-well-at-lowest-cost. Assignments: Task 2 → Haiku; Tasks 1, 3–8 → Sonnet (Task 4 additionally gets a reviewer); Task 9 → orchestrator (Fable). Escalate a task's model only after a cheap attempt actually fails, not preemptively.
- **Commits:** one per task; **the `--author` tier names the model that typed the diff** — `--author="Claude (Sonnet 5) <noreply@anthropic.com>"` for Sonnet-implemented tasks, `Claude (Haiku 4.5)` for Task 2, `Claude (Fable 5)` only for orchestrator-authored commits (Task 9 walk fixes). Stage exactly the task's files, never `git add -A`.
- **localStorage key:** `introspect.sidebarTree.v1` — `'1'` = tree on; key ABSENT = flat (never write `'0'`).
- **Toggle label copy:** `by project` (exact, lowercase). Tree ordering: **alphabetical** by display name.
- **Spec §8.1 binds Task 9:** aesthetic passes — minor fixes silent, larger ideas described not executed, max 3 passes.

---

### Task 1: `/projects` counts exclude archived sessions

**Files:**
- Modify: `server/src/introspect/api/routes/sessions.py:213-231` (`list_projects`)
- Test: `server/tests/test_api_sessions.py`

**Interfaces:**
- Consumes: `_not_archived()` (`sessions.py:153`), existing `client` fixture + `PROJECT_SLUG_*`/session-uuid constants already defined at the top of `test_api_sessions.py`.
- Produces: `GET /api/v1/projects` whose `session_count` counts only non-archived sessions. No shape change to `ProjectOut`.

- [ ] **Step 1: Write the failing test** (in `test_api_sessions.py`, beside `test_projects_lists_session_counts` at :74; reuse that test's constants for a project slug and one of its session uuids)

```python
def test_projects_session_counts_exclude_archived(client: TestClient) -> None:
    """The tree's count badge must agree with the children a project shows (spec §6.1):
    an archived session vanishes from the sessions list, so it must not be counted."""
    before = {p["dir_slug"]: p["session_count"] for p in client.get("/api/v1/projects").json()}
    resp = client.put(f"/api/v1/sessions/{SESSION_UUID_1}/archive")
    assert resp.status_code == 204
    after = {p["dir_slug"]: p["session_count"] for p in client.get("/api/v1/projects").json()}
    assert after[PROJECT_SLUG_1] == before[PROJECT_SLUG_1] - 1
    # Other projects untouched.
    for slug, count in after.items():
        if slug != PROJECT_SLUG_1:
            assert count == before[slug]
```

(If the file's constants are named differently, match the file — the *shape* above is the contract. The archive verb path is the one `test_api_archive.py` exercises.)

- [ ] **Step 2: Run it — expect FAIL** (`after == before`, archived row still counted): `uv run pytest tests/test_api_sessions.py::test_projects_session_counts_exclude_archived -q`

- [ ] **Step 3: Implement** — move the exclusion into the outerjoin's ON clause so projects with zero live sessions still appear with count 0:

```python
.outerjoin(
    ChatSession,
    and_(ChatSession.project_id == Project.id, _not_archived()),
)
```

(`and_` may need adding to the existing `sqlalchemy` import line. `_not_archived()` correlates against `ChatSession`, which is in scope inside the ON clause.)

- [ ] **Step 4: Run the test — PASS; then the whole file:** `uv run pytest tests/test_api_sessions.py -q` (the pre-existing `test_projects_lists_session_counts` must still pass — no fixture session is archived by default).

- [ ] **Step 5: Commit** — `server: /projects counts exclude archived sessions (tree badge honesty, spec §6.1)`

---

### Task 2: `useSidebarTree` hook

**Files:**
- Create: `web/src/lib/sidebarTree.ts`
- Test: `web/tests/sidebarTree.test.ts`

**Interfaces:**
- Consumes: nothing (framework-free except React).
- Produces: `useSidebarTree(): readonly [boolean, (value: boolean) => void]` — ONE owner per mount (Sidebar, Task 7); `STORAGE_KEY = 'introspect.sidebarTree.v1'` not exported.

- [ ] **Step 1: Write failing tests**

```ts
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useSidebarTree } from '../src/lib/sidebarTree'

const KEY = 'introspect.sidebarTree.v1'

describe('useSidebarTree', () => {
  afterEach(() => window.localStorage.removeItem(KEY))

  it('defaults to flat (false) when the key is absent', () => {
    const { result } = renderHook(() => useSidebarTree())
    expect(result.current[0]).toBe(false)
  })

  it('seeds true from a stored "1"', () => {
    window.localStorage.setItem(KEY, '1')
    const { result } = renderHook(() => useSidebarTree())
    expect(result.current[0]).toBe(true)
  })

  it('setting true writes "1"; setting false REMOVES the key (absent === off)', () => {
    const { result } = renderHook(() => useSidebarTree())
    act(() => result.current[1](true))
    expect(window.localStorage.getItem(KEY)).toBe('1')
    act(() => result.current[1](false))
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})
```

- [ ] **Step 2: Run — expect FAIL** (module not found): `npx vitest run tests/sidebarTree.test.ts`

- [ ] **Step 3: Implement** — clone the `chatOnly.ts` pattern exactly (read `web/src/lib/chatOnly.ts:40-73` first: `readStored`/`writeStored` in try/catch degrading to in-memory, `useState(readStored)`, `useCallback` setter). Same doc-comment discipline: state model is ONE owner (Sidebar) threading `[treeMode, setTreeMode]` down as props. Omit anything chat-only-specific (`isChatOnlyVisible` has no analogue here).

- [ ] **Step 4: Run — PASS**, then `npx vitest run` (full suite green).

- [ ] **Step 5: Commit** — `web: useSidebarTree — introspect.sidebarTree.v1, chatOnly persistence pattern (spec §4.3)`

---

### Task 3: `projectDisplayName` extraction + `SessionListItem.inTree`

**Files:**
- Create: `web/src/lib/projectName.ts`
- Modify: `web/src/components/SessionListItem.tsx` (delete local `projectEyebrow` :172-181; add prop)
- Test: `web/tests/projectName.test.ts`, `web/tests/SessionListItem.test.tsx` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `projectDisplayName(slug: string): string` from `web/src/lib/projectName.ts`; `SessionListItemProps` gains `inTree?: boolean` (default false) — when true the project-eyebrow line is not rendered.

- [ ] **Step 1: Write failing tests**

`tests/projectName.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { projectDisplayName } from '../src/lib/projectName'

describe('projectDisplayName', () => {
  it('cuts after the last -Users- marker', () => {
    expect(projectDisplayName('-Users-donovan-projects--ai-jetwalls')).toBe(
      'donovan-projects--ai-jetwalls',
    )
  })
  it('returns the slug verbatim when no marker exists', () => {
    expect(projectDisplayName('plain-slug')).toBe('plain-slug')
  })
})
```

In `tests/SessionListItem.test.tsx`, add (following that file's existing render harness):
```ts
it('inTree suppresses the project eyebrow line', () => {
  renderItem({ ...SESSION, project_slug: '-Users-x-proj' }, { inTree: true })
  expect(screen.queryByText('x-proj')).toBeNull()
})
```
(Adapt `renderItem` to pass the prop through however that file's helper is shaped; if it renders bare JSX, add `inTree` at the call site.)

- [ ] **Step 2: Run — expect FAIL:** `npx vitest run tests/projectName.test.ts tests/SessionListItem.test.tsx`

- [ ] **Step 3: Implement**

`web/src/lib/projectName.ts` — move the function + its doc comment verbatim from `SessionListItem.tsx:172-181`, renamed:
```ts
/** `project_slug` is the CLI's raw source-directory name, e.g. "-Users-x-proj". The display name
 * is the tail after the last "-Users-" — deliberately not a full path reconstruction (the CLI's
 * dash-collapsing isn't reliably reversible); the simple, robust cut. Shared by the sidebar
 * row eyebrow and the project-tree row label (spec §4.4). */
export function projectDisplayName(slug: string): string {
  const marker = '-Users-'
  const idx = slug.lastIndexOf(marker)
  return idx === -1 ? slug : slug.slice(idx + marker.length)
}
```

`SessionListItem.tsx`: delete the local `projectEyebrow`, import `projectDisplayName`, add `inTree` to props with a doc line ("rendered under its project row — the eyebrow would repeat the parent"), and wrap the eyebrow div: `{!inTree && (<div …>{projectDisplayName(session.project_slug)}</div>)}`.

- [ ] **Step 4: Run — PASS**, full suite green.

- [ ] **Step 5: Commit** — `web: projectDisplayName shared helper + SessionListItem inTree (spec §4.2/§4.4)`

---

### Task 4: `TopbarSearch` — search box moves to the topbar

**Files:**
- Create: `web/src/components/TopbarSearch.tsx`
- Modify: `web/src/App.tsx:32-36`, `web/src/App.css:12-27`, `web/src/components/Sidebar.tsx` (delete input + debounce)
- Test: `web/tests/TopbarSearch.test.tsx` (new), `web/tests/Sidebar.test.tsx` (rework input-driven tests)

**Interfaces:**
- Consumes: `readSidebarParams`/`writeSidebarParams` (`lib/urlState.ts`), `.sw-input` class (`theme.css:57`).
- Produces: `TopbarSearch()` — no props; owns the input, the 250ms debounce, and ALL `?filter=` writes. After this task `Sidebar` never writes `?filter=` — it only reads it.

- [ ] **Step 1: Write failing tests** — `tests/TopbarSearch.test.tsx` ports the debounce/echo tests from `tests/Sidebar.test.tsx` (the `writeSidebarParamsSpy` hoisted-mock pattern at :29-38 moves here with them): typed input → exactly ONE URL write after 250ms (fake timers); mount with `?filter=x` seeds the input value; clearing writes a filter-delete. Keep the spy assertions identical — the echo regression (`Sidebar.tsx:25-31`) must stay pinned.

- [ ] **Step 2: Run — expect FAIL** (component doesn't exist).

- [ ] **Step 3: Implement**

`TopbarSearch.tsx` — the input JSX (`Sidebar.tsx:92-106`), the `filterInput` state + `lastWrittenFilter` ref + debounce effect (`Sidebar.tsx:19-44`) move here VERBATIM including the referential-instability comment, minus `setDebouncedFilter` (gone — the URL is now the debounced channel). Width: `width: 276` (aligns over the 300px sidebar column minus the row's 12px padding), not `100%`.

`App.tsx`:
```tsx
<div className="topbar-row">
  <TopbarSearch />
  <ProjectFilterBar />
</div>
```

`App.css`: replace the `.app > .project-filter-bar` block (:15-17) with `.app > .topbar-row { grid-area: topbar; display: flex; align-items: flex-start; gap: 10px; padding: 7px 12px; background: var(--surface); border-bottom: 1px solid var(--shore); }`; the `.project-filter-bar` block (:19-27) loses `padding`/`background`/`border-bottom` (now the row's) and gains `flex: 1`.

`Sidebar.tsx`: delete the input, `filterInput`, `debouncedFilter`, `lastWrittenFilter`, the effect, and `DEBOUNCE_MS`; `const { filter, fav } = readSidebarParams(searchParams)` drives the query directly (`q: filter || undefined`); `hasFilter = filter.length > 0 || fav`.

- [ ] **Step 4: Rework `tests/Sidebar.test.tsx`:** delete the moved input tests; convert query-driving tests to mount at `initialEntries={['/?filter=foo']}`. Run both files + full suite — PASS.

- [ ] **Step 5: Commit** — `web: TopbarSearch — filter box moves to topbar row, left of project chips; Sidebar becomes a ?filter= reader (spec §4.1, D1)`

---

### Task 5: `ProjectTree` — browse mode

**Files:**
- Create: `web/src/components/ProjectTree.tsx`
- Test: `web/tests/ProjectTree.test.tsx`

**Interfaces:**
- Consumes: `useProjects()`/`useSessions()` (`api/hooks.ts`), `projectDisplayName` (Task 3), `SessionListItem` + `inTree` (Task 3), `ProjectOut`/`SessionSummary` (`api/types.ts`).
- Produces: `ProjectTree({ q, fav, chips, search }: ProjectTreeProps)` where `q: string` (current filter text), `fav: boolean`, `chips: string[]` (selected project slugs), `search: string` (current query string incl. `?`, threaded to `SessionListItem`). Filtered mode (`q || fav`) is Task 6 — this task renders browse mode and a `FilteredTree` STUB returning `null` with a `// Task 6 un-stubs me` marker (honest stub, never a lying cast).

- [ ] **Step 1: Write failing tests** (mock `api/client`'s `fetchProjects` + `fetchSessions` per the Sidebar.test.tsx harness pattern; wrap in QueryClientProvider + MemoryRouter):

```ts
// Fixtures: three ProjectOut rows, DELIBERATELY unsorted by display name:
//   { id: 2, dir_slug: '-Users-x-zeta',  resolved_cwd: null, session_count: 2 }
//   { id: 1, dir_slug: '-Users-x-alpha', resolved_cwd: null, session_count: 41 }
//   { id: 3, dir_slug: '-Users-x-mid',   resolved_cwd: null, session_count: 0 }

it('renders every project alphabetically by display name with count badges', ...)
  // order on screen: alpha, mid, zeta — asserts sort + zero-count project still visible (D2)

it('rows start collapsed; no sessions fetch fires until expand', ...)
  // fetchSessions not called after initial render

it('expand fetches exactly that project once and renders children inTree', ...)
  // click ▸ on alpha → fetchSessions called with { projects: ['-Users-x-alpha'] } →
  // child rows render WITHOUT eyebrow text; collapse + re-expand → no second fetch (cache)

it('shows "showing N of M" only when total exceeds the window', ...)
  // fetchSessions resolves { items: [50 rows], total: 213 } → 'showing 50 of 213' present;
  // total === items.length → absent

it('chips scope the project rows client-side', ...)
  // chips={['-Users-x-zeta']} → only zeta renders

it('a failed children fetch shows an inline retry row without hiding siblings', ...)
  // fetchSessions rejects for alpha → alpha subtree shows 'failed to load — retry';
  // zeta row still present; clicking retry refetches
```

Write these as real tests with the harness's `pageOf`-style fixture builders; every `it` above is required.

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** `ProjectTree.tsx`:

```tsx
export interface ProjectTreeProps { q: string; fav: boolean; chips: string[]; search: string }

export function ProjectTree({ q, fav, chips, search }: ProjectTreeProps) {
  // Manual expand state survives filtered-mode roundtrips: FilteredTree never touches it (D3).
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const toggle = (slug: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  if (q.length > 0 || fav) return <FilteredTree q={q} fav={fav} chips={chips} search={search} />
  return <BrowseTree chips={chips} search={search} expanded={expanded} onToggle={toggle} />
}
```

`BrowseTree`: `useProjects()`; loading → the Sidebar `SkeletonRows` idiom (copy the 3-row static block; it is 18 lines, duplication beats exporting a one-off); error → `<p>archive offline</p>` with the `MIST_TEXT` style values; rows = `(chips.length ? data.filter((p) => chips.includes(p.dir_slug)) : data).slice().sort((a, b) => projectDisplayName(a.dir_slug).localeCompare(projectDisplayName(b.dir_slug)))`. Each row: a full-width `<button type="button" aria-expanded={open} onClick={() => onToggle(p.dir_slug)}>` in the mono register (`fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--moonpaper)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, width: '100%', padding: '6px 6px', textAlign: 'left'`), containing the `▸`/`▾` glyph (`color: 'var(--mist)'`), the display name, and a right-aligned count `<span style={{ marginLeft: 'auto', color: 'var(--mist)', fontSize: 11 }}>`. When open, render `<ProjectChildren slug={p.dir_slug} search={search} />` in a `<div style={{ paddingLeft: 14 }}>`.

`ProjectChildren`: `const { data, isLoading, isError, refetch } = useSessions({ projects: [slug] })`; loading → one skeleton row; error → `<button type="button" onClick={() => refetch()}>failed to load — retry</button>` (mist, bare); success → `data.items.map((s) => <SessionListItem key={s.session_uuid} session={s} search={search} inTree />)` plus `{data.total > data.items.length && (<p style={…mist, fontSize: 11, padding: '2px 6px'}}>showing {data.items.length} of {data.total}</p>)}`.

`FilteredTree` stub: `function FilteredTree(_props: ProjectTreeProps) { return null } // Task 6 un-stubs me`.

- [ ] **Step 4: Run — PASS**, full suite green.

- [ ] **Step 5: Commit** — `web: ProjectTree browse mode — lazy per-project children, alphabetical, count badges, showing-N-of-M (spec §4.4, D2)`

---

### Task 6: `ProjectTree` — filtered mode (prune + auto-expand)

**Files:**
- Modify: `web/src/components/ProjectTree.tsx` (replace the `FilteredTree` stub)
- Test: `web/tests/ProjectTree.test.tsx` (extend)

**Interfaces:**
- Consumes: Task 5's `ProjectTreeProps`, `SessionListItem inTree`, `renderSnippet` already exercised via SessionListItem.
- Produces: filtered rendering per spec §4.5. No API surface change.

- [ ] **Step 1: Write failing tests** (same file):

```ts
it('q or fav switches to ONE flat query grouped by project, auto-expanded', ...)
  // q='horizon' → fetchSessions called ONCE with { q: 'horizon' } (NOT per-project);
  // fetchProjects NOT required; result rows from two slugs render under two group headers,
  // both open, session rows inTree

it('groups sort alphabetically and absent projects are pruned', ...)
  // result contains slugs zeta+alpha only → headers alpha, zeta; no 'mid' header anywhere

it('snippets ride into the grouped rows', ...)
  // a row with match_snippet renders its <mark> content (SessionListItem does the work —
  // assert the marked text is on screen)

it('chips and fav thread into the query', ...)
  // chips=['-Users-x-alpha'], fav=true, q='' → fetchSessions called with
  // { favorite: true, projects: ['-Users-x-alpha'] }

it('truncation line renders when total > items', ...)
  // { items: […], total: 80 } → 'showing 12 of 80 matches' (items.length interpolated)

it('clearing the filter restores browse mode with manual expand state intact', ...)
  // expand zeta in browse → rerender with q='x' (filtered) → rerender back with q='' →
  // zeta still expanded, no refetch of zeta's children (cache)
```

- [ ] **Step 2: Run — expect FAIL** (stub returns null).

- [ ] **Step 3: Implement** `FilteredTree`:

```tsx
function FilteredTree({ q, fav, chips, search }: ProjectTreeProps) {
  const { data, isLoading, isError } = useSessions({
    q: q || undefined,
    favorite: fav || undefined,
    ...(chips.length > 0 ? { projects: chips } : {}),
  })
  if (isLoading) return <SkeletonRows />
  if (isError) return <p style={MIST_TEXT}>archive offline</p>
  if (data.items.length === 0) return <p style={MIST_TEXT}>No conversations match</p>

  const groups = new Map<string, SessionSummary[]>()
  for (const item of data.items) {
    const list = groups.get(item.project_slug) ?? []
    list.push(item)
    groups.set(item.project_slug, list)
  }
  const slugs = [...groups.keys()].sort((a, b) =>
    projectDisplayName(a).localeCompare(projectDisplayName(b)),
  )
  return (
    <>
      {slugs.map((slug) => (
        <div key={slug}>
          {/* Static ▾ — auto-expanded groups are not collapsible while filtering (D3); a
              disclosure that can't close would lie as a button, so this is a heading div. */}
          <div style={GROUP_HEADER_STYLE}>▾ {projectDisplayName(slug)}</div>
          <div style={{ paddingLeft: 14 }}>
            {groups.get(slug)!.map((s) => (
              <SessionListItem key={s.session_uuid} session={s} search={search} inTree />
            ))}
          </div>
        </div>
      ))}
      {data.total > data.items.length && (
        <p style={TRUNCATION_STYLE}>
          showing {data.items.length} of {data.total} matches
        </p>
      )}
    </>
  )
}
```

(`GROUP_HEADER_STYLE`/`TRUNCATION_STYLE`: the mono/mist values already used in Task 5's rows — hoist shared consts rather than repeating literals. Group order preserves alphabetical to match browse mode; recency *within* a group is the server's row order, untouched.)

- [ ] **Step 4: Run — PASS**, full suite green.

- [ ] **Step 5: Commit** — `web: ProjectTree filtered mode — prune + auto-expand over the flat query, snippets riding (spec §4.5, D3)`

---

### Task 7: Sidebar integration — toggle, body switch, cache invalidations

**Files:**
- Modify: `web/src/components/Sidebar.tsx`, `web/src/components/StatusBar.tsx:99-103`, `web/src/api/hooks.ts:174-183` (`useArchiveSession`)
- Test: `web/tests/Sidebar.test.tsx` (extend), `web/tests/StatusBar.test.tsx` (extend)

**Interfaces:**
- Consumes: `useSidebarTree` (Task 2), `ProjectTree` (Tasks 5-6).
- Produces: the shipped feature — toggle pill labeled `by project` in the All/★ row; `['projects']` invalidated on import success and archive success.

- [ ] **Step 1: Write failing tests**

`Sidebar.test.tsx`:
```ts
it('renders the by-project toggle with aria-pressed from storage', ...)
  // localStorage '1' → toggle pressed, ProjectTree body (fetchProjects called, no flat rows)
it('toggle click flips mode, persists, and swaps the body', ...)
  // starts flat (fetchSessions called, fetchProjects not) → click 'by project' →
  // fetchProjects called; localStorage has '1'; click again → key removed, flat body back
it('flat mode is byte-identical to today', ...)
  // OFF: existing flat-list assertions still hold (this is the regression pin)
```

`StatusBar.test.tsx`: extend the import-success test to assert `['projects']` is invalidated (spy on `queryClient.invalidateQueries` the way that file already observes the `['status']`/`['sessions']` calls, or assert a mounted `useProjects` consumer refetches).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

`Sidebar.tsx`: `const [treeMode, setTreeMode] = useSidebarTree()` (the ONE owner). In the All/★ row (`Sidebar.tsx:108-129`), append right-aligned:
```tsx
<button
  type="button"
  className="tree-toggle"
  aria-pressed={treeMode}
  onClick={() => setTreeMode(!treeMode)}
  style={{ ...chipStyle(treeMode), marginLeft: 'auto' }}
>
  by project
</button>
```
Body: `{treeMode ? <ProjectTree q={filter} fav={fav} chips={projects} search={search ? `?${search}` : ''} /> : (<div className="convo-list" …existing flat block unchanged…</div>)}`.

`StatusBar.tsx` (:101-102): add `queryClient.invalidateQueries({ queryKey: ['projects'] })` beside the existing two, with a one-line why ("a run can discover a new project — the tree and chip bar must see it"). Delete the now-false "no invalidation hookup" paragraph in `hooks.ts:125-129` and replace with a line pointing at StatusBar. `useArchiveSession` (:174-183): add the same invalidation ("archiving changes per-project counts").

- [ ] **Step 4: Run both test files + full suite — PASS.**

- [ ] **Step 5: Commit** — `web: sidebar tree toggle (by project) + ['projects'] invalidation on import/archive (spec §4.3/§6.2)`

---

### Task 8: Documentation

**Files:**
- Modify: `docs/user/reading-room.md` (sidebar section), `README.md` (the reading-room feature bullets that describe the sidebar search box)

**Interfaces:** none — prose only, but grounded: describe the SHIPPED behavior from Tasks 4-7, not the spec's intentions.

- [ ] **Step 1:** Update `reading-room.md`'s sidebar section: search box now in the topbar (left of the chips); the `by project` toggle — browse behavior, lazy loading, count badges, `showing N of M`, filtered prune + auto-expand; sticky via localStorage; All/★ and chips orthogonal to view mode.
- [ ] **Step 2:** Sweep `README.md` for sentences placing the search box inside the sidebar; update. `grep -rn "Filter by title" README.md docs/` to catch strays.
- [ ] **Step 3:** Commit — `docs: sidebar project tree + topbar search relocation (spec §10)`

---

### Task 9: Live walk + aesthetic passes — ORCHESTRATOR-OWNED (not a subagent task)

**Files:** none planned — findings drive fixes.

- [ ] **Step 1:** Dev server: `cd web && npm run dev` — vite proxies `/api` → `:8765` (`vite.config.ts:7-10`), so the running TUI server feeds HMR. Open `http://localhost:5173` via chrome-mcp (per relativityboy: server already up via TUI; "as simple as opening a tab").
- [ ] **Step 2:** Functional walk (spec §8): toggle on → browse → expand two projects (one large: `showing 50 of N` visible) → type a filter (prune + auto-expand + snippets) → ★ Favorites in tree mode → clear (manual expand state restored) → chips scope the tree → toggle off (flat view unchanged from today).
- [ ] **Step 3:** Aesthetic passes per spec §8.1 — "Do I like the way this looks? What could make it nicer/tidier/better?" Minor fixes applied silently (with tests updated where they pin styles); larger ideas DESCRIBED to relativityboy for prioritization, never self-executed. **Max 3 passes.**
- [ ] **Step 4:** `npm run build` and spot-check `:8765` serves the built tree (production path, not just HMR).
- [ ] **Step 5:** Final: full web + server suites green; commit any walk fixes as `web: tree walk fixes — <what>`.

---

## Self-review (performed at write time)

- **Spec coverage:** D1→T4, D2→T1+T5, D3→T6, D4→T7; §4.1→T4, §4.2→T3/T7, §4.3→T2/T7, §4.4→T5, §4.5→T6, §4.6→T5 (aria-expanded)/T7 (aria-pressed), §5→T5-7 wiring, §6.1→T1, §6.2→T7, §7→T5/T6 error rows, §8→T9, §8.1→T9, §10→T8. §9's out-of-scope list has no tasks — correct.
- **Placeholders:** the only stub is Task 5's `FilteredTree` returning `null`, explicitly marked and un-stubbed by Task 6 — an honest stub per the repo's forward-reference rule.
- **Type consistency:** `ProjectTreeProps { q, fav, chips, search }` identical in T5/T6/T7 call site; `useSidebarTree` tuple shape matches T2 across T7; `projectDisplayName` name matches T3 across T5/T6; `inTree` prop name consistent T3/T5/T6.
