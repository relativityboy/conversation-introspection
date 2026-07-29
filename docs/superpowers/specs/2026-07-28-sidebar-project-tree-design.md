# Sidebar project tree — design

**Date:** 2026-07-28
**Status:** approved by relativityboy (brainstorm 2026-07-26→28); implementation planned separately
**Feature:** a toggleable directory-style view for the sidebar — `<project>/<sessions>` — alongside
the existing flat recency list, plus relocation of the sidebar search box into the topbar.

## 1. Intent

The sidebar today is a single flat list: the 50 most-recent sessions across every project. That
serves "what was I just doing" and nothing else. The tree serves "show me this project's
conversations" — a real directory: every project visible, sessions nested under it, each project
with its own recency window instead of competing for one global one.

## 2. Decisions of record

These were explicit forks, ruled on during brainstorming:

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Where does the search box go? | Out of the sidebar entirely, into the **topbar row, left of the project chip bar** | relativityboy's call; the chip bar already lives in the topbar (`App.tsx:33-36`), so project-level controls cluster in one row |
| D2 | What feeds the tree? | **True directory, lazy** — projects from `GET /projects`, children fetched per-project on expand | Grouping the existing top-50 window only *looks* like a directory: projects outside the window vanish, subtrees are silently partial. Rejected. |
| D3 | Tree under active filters (search text / ★)? | **Prune + auto-expand** — only projects containing matches remain, open, snippets visible | File-explorer-search behavior; the alternative (static tree, expand-to-discover) hides matches behind N clicks |
| D4 | View-mode toggle semantics | Toggle in the sidebar; **Favorites/All and project chips work identically in both views** | relativityboy's framing in the original ask |

## 3. Options considered (approach)

**A — client composition over existing endpoints (CHOSEN).** The tree is orchestration:
browse mode = `GET /projects` + per-project `GET /sessions?projects=<slug>` on expand (react-query
caches each subtree); filtered mode = the same single query the flat view runs, grouped by
`project_slug` client-side. Two small server touches (§6). Rationale: `?projects=` and `?filter=`
already compose correctly at both the SQL and FTS layers (`sessions.py:245,271-287`,
`fts5.py:225-233`) — a new endpoint would duplicate tested semantics. Cost accepted: expand is a
fetch (cached after first); a filtered tree truncates at the same top-50 contract the flat view
has today, with `total` available to say so.

**B — dedicated `GET /sessions/tree` endpoint (REJECTED for v1).** One round-trip, counts
consistent by construction. Rejected because it adds server surface duplicating existing query
semantics, re-sends every subtree per filter keystroke, and nothing about the tree needs
server-side atomicity. Revisit only if per-project windows ever need server-side stitching.

**C — group the current 50-row window client-side (REJECTED).** Zero plumbing, fastest ship, and
wrong: it presents a partial view in directory costume (see D2).

## 4. UI design

### 4.1 Topbar

Grid stays three-area (`App.css:1-10`). The topbar row becomes `[TopbarSearch][ProjectFilterBar]`:

- **New `TopbarSearch` component** owns the search input, the 250ms debounce, and the `?filter=`
  URL write — moved wholesale from `Sidebar.tsx:22-44,92-106` (same `urlState` helpers, same
  placeholder text, same echo-guard ref). `Sidebar` becomes a pure *reader* of `?filter=`.
- Input width ~300px, visually aligned over the sidebar column it filters; chip bar keeps the
  remainder of the row. Chip bar's layered-Escape behavior untouched.

### 4.2 Sidebar

Top to bottom: app-title link (unchanged) → one row holding the All/★ chips and, right-aligned,
the **view toggle** → the list body, which is either:

- **`FlatList`** — today's `.convo-list` rendering, unchanged; or
- **`ProjectTree`** — new, below.

`SessionListItem` is reused by both, gaining an `inTree` prop that suppresses the project-eyebrow
line (the parent project row already names the project).

### 4.3 View toggle

- Small pill in the All/★ row, `aria-pressed`, label **`by project`** (words over glyphs, matching
  the calm mono register). ON = tree.
- Persistence: `localStorage` key **`introspect.sidebarTree.v1`** via a `useSidebarTree()` hook
  cloned from the `chatOnly.ts` pattern — `'1'` = on, key absent = flat, reads/writes in try/catch
  degrading to in-memory, ONE owner (`Sidebar`), threaded down as props.
- Deliberately **not** URL state: view mode is a device preference; a shared `?filter=` link must
  work identically in either mode.

### 4.4 ProjectTree — browse mode (no search text, no ★)

- Project rows from `useProjects()`: disclosure `▸/▾` button, display name, count badge.
- Display name = slug tail (cut after last `-Users-`), extracted from `SessionListItem.tsx:177-181`
  into a shared `lib/` helper — one implementation, both consumers.
- **Ordering: alphabetical by display name.** A directory is stable spatial structure; recency
  reordering makes projects jump between visits, and recency already has a home (flat view).
  Alphabetical also needs no new server field.
- Expand fetches that project's children via the existing sessions query with
  `projects=[slug]` — its own query key, cached, skeleton rows while loading.
- Children capped at the server's 50-row window; when `total > rows.length` render a quiet
  `showing 50 of N` line (the `total` the flat view ignores — `Sidebar.tsx:142-149` — gets used).
  No pagination in v1.
- Expand state: in-memory `Set<slug>` per mount (the `ToolBlock.tsx:40-72` precedent). Not
  persisted, not URL.
- Project chips (`?projects=`) scope browse mode by filtering the project rows client-side to the
  selected slugs.

### 4.5 ProjectTree — filtered mode (search text and/or ★ active)

- Runs the **flat view's exact query** (`q`, `favorite`, `projects`) once; groups result rows by
  `project_slug`; renders every group expanded with match snippets exactly as the flat rows carry
  them today.
- Projects with no matching sessions are absent (D3 prune). Clearing the filter returns to browse
  mode with the user's manual expand state intact (the `Set` is never mutated by filtered mode).
- Truncation contract identical to today's flat view (top 50 by recency across the filtered set);
  when `total > 50`, one `showing 50 of N matches` line at the bottom of the tree.

### 4.6 Accessibility

Disclosure buttons carry `aria-expanded`; the toggle carries `aria-pressed`. Deliberately NOT the
full ARIA `tree`/`treeitem` grammar — that contract mandates arrow-key roving focus, which is more
machinery than a two-level disclosure list warrants in v1. Native tab order.

## 5. Data flow

```
TopbarSearch ──writes──▶ ?filter= ─┐
ProjectFilterBar ──▶ ?projects= ───┼─▶ Sidebar (reads URL)
All/★ chips ──────▶ ?fav= ─────────┘        │
                                            ├─ flat  ──▶ useSessions({q, favorite, projects})   [today's path]
useSidebarTree() ◀─ localStorage            └─ tree ──┬─ browse:   useProjects()  +  per-expand
                                                      │            useSessions({projects:[slug]})
                                                      └─ filtered: useSessions({q, favorite, projects})
                                                                   → groupBy(project_slug)
```

No new endpoints. No new URL params. One new localStorage key.

## 6. Server touches (two, both small)

1. **`/projects` counts exclude archived sessions** — `sessions.py:220-225` gains the
   `_not_archived()` guard the list route already applies; without it the tree's count badges
   disagree with the children shown. Pytest asserts an archived session is not counted.
2. **Invalidate `['projects']`** on import-success and archive-success mutations — `/projects` is
   `staleTime: Infinity` and never invalidated (`hooks.ts:125-136`), a standing staleness the chip
   bar already suffers (new projects invisible until reload). The tree makes it unacceptable.

## 7. Error handling

- `useProjects()` failure in browse mode → the existing `archive offline` treatment in the list
  area (same as flat view's error state).
- A single subtree's fetch failure → inline error row inside that project only, click-to-retry
  (react-query `refetch`); sibling projects unaffected.
- Filtered-mode failure → identical to flat view's error state today.

## 8. Testing

Web (vitest, existing harness patterns — mock `api/client`, real hooks/router):

- `TopbarSearch`: debounced `?filter=` write + URL-seed on mount (port existing Sidebar search tests).
- Toggle: `aria-pressed`, localStorage persistence via `introspect.sidebarTree.v1`, absent = flat.
- Browse mode: projects render alphabetically from mocked `useProjects`; expand triggers exactly
  one sessions fetch with `projects=[slug]`; collapse+re-expand does not refetch (cache); count
  badge renders; `showing 50 of N` renders iff `total > rows`.
- Filtered mode: rows group by project, all groups expanded, snippets render, non-matching
  projects absent; clearing filter restores manual expand state.
- Chips scope both modes.
- `SessionListItem inTree`: eyebrow suppressed.

Server (pytest): archived sessions excluded from `/projects` `session_count`.

Live verification (relativityboy's instruction): **chrome-mcp** against the running TUI server.
Check whether the vite dev server (`npm run dev`) proxies `/api` to :8765 for HMR; if not,
`npm run build` per look (~0.4s) and verify against :8765 directly. Walk: toggle on → browse →
expand two projects → filter prunes/auto-expands → clear restores → toggle off → flat view
byte-identical to today.

### 8.1 Aesthetic passes (relativityboy's execution rule — applies to implementers AND the orchestrator)

During live verification, evaluate the *overall* appearance, not just correctness: **"Do I like the
way this looks? What could make it nicer/tidier/better?"**

- **Minor improvements** (spacing, alignment, token choice, hover/focus states, truncation
  behavior) — just do them, no check-in, staying inside the Still Water language.
- **Larger improvements** (layout shifts, new affordances, anything a user would notice as a
  different design) — describe them with enough detail to prioritize; do NOT self-execute.
- **At most 3 passes** of this general polish. Then stop — remaining ideas go on the
  prioritization list, not into the diff.

## 9. Out of scope (named, not implied)

- Per-project pagination / "load more" inside a subtree.
- Persisting expand state across reloads.
- Auto-expanding the project of the currently-open session (nice orientation touch; revisit).
- Recency ordering of projects (flip of §4.4 if alphabetical feels wrong in use).
- ARIA tree grammar with roving focus.
- Any change to the reader, search page, or subagent views.

## 10. Documentation

`docs/user/reading-room.md` sidebar section updated as part of implementation (user-visible
feature). This spec records the options and decisions per relativityboy's request.
