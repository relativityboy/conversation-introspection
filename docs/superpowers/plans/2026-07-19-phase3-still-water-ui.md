# Phase 3: Still Water Reading Room — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Git rule:** Donovan owns pushes; the controller commits at checkpoints (authored as Claude). Workers only `git add` — never commit.

**Goal:** The React reading room over the Phase 2 API — Still Water theme, sidebar + two-tab main area + waterline status bar, a serif conversation reader with honest block rendering, URL-deep-linkable everything.

**Architecture:** Vite + React + TypeScript SPA in `web/`, talking only to `/api/v1` (Phase 2). Design authority is the approved mockup `docs/design/2026-07-13-still-water-mockup.html` — its CSS custom properties, typography stacks, and component treatments are the visual contract (one approved amendment: sidebar micro-bands recede). Server-side: one small addition — FastAPI serves `web/dist` when present.

**Tech Stack:** Vite, React 18+, TypeScript strict. New runtime deps (each justified, Opus may challenge): `react-router-dom` (URL scheme IS the feature), `@tanstack/react-query` (server-state caching/pagination/polling for 10+ endpoints — hand-rolling this is the over-engineering), `react-virtuoso` (sessions reach 900+ messages; virtualization is a correctness need, not polish), `react-markdown` + `remark-gfm` + `rehype-highlight` (conversation prose is markdown with code). Dev: vitest, @testing-library/react, @testing-library/user-event, jsdom, eslint, prettier.

**Spec:** design doc §9 (authority) + the mockup. Phase 2 API shapes are binding contracts — read them from `server/src/introspect/api/models.py` and routes, not from memory.

## Global Constraints

- **Zero external network at runtime.** No CDN, no webfonts, no analytics. System font stacks per the mockup (Charter/Iowan for prose, system-ui chrome, ui-monospace terminal material). The app functions fully offline against localhost.
- **Still Water tokens verbatim from the mockup** (`--depth #0B1220`, `--surface #111B2E`, `--shore #1A2740`, `--moonpaper #E7ECF4`, `--mist #8A99B0`, `--dawn #E5A54B`, `--dragonfly #6FD3C7`, `--ember #D4756B`). Voice accents: dawn = user, dragonfly = assistant. Dark-only v1.
- **All view state that identifies content lives in the URL** (spec §9): route params + query strings. Refresh/share must reproduce the view. Main-area searches commit on Enter; sidebar title filter is as-you-type (debounced 250ms) and also URL-synced (replace, not push).
- **Honest rendering:** thinking = glyph ◌ + "thinking occurred — content not persisted by the CLI" (never an empty expando); tool blocks collapsed by default; subagent transcripts fetched ONLY on drill-in (lazy — they can be 1.4MB).
- Reduced-motion respected (`prefers-reduced-motion` kills the deep-link glow and any transition). `:focus-visible` rings (`--dragonfly`) on all interactives. Semantic landmarks (nav/main/footer), `aria-selected` tabs.
- TypeScript strict; no `any` without a NOTE justifying it. eslint+prettier clean. Component tests (vitest+RTL) for every logic-bearing behavior named in a task; no snapshot tests (they assert nothing).
- API base path `/api/v1` via Vite dev proxy to `127.0.0.1:8765`; no hardcoded hosts in source.
- Problem-JSON errors surface as inline error states with `title` text — never blank screens, never raw JSON dumps.

## File Structure

```
web/
  package.json  vite.config.ts  tsconfig.json  index.html  .eslintrc / prettier config
  src/
    main.tsx  App.tsx                    # router + shell grid
    theme.css                           # :root tokens + reset + base typography (from mockup)
    api/
      types.ts                          # TS mirrors of api/models.py shapes + Problem
      client.ts                         # fetch wrappers, Problem-aware error class
      hooks.ts                          # react-query hooks per endpoint
    components/
      HorizonBand.tsx                   # signature: day-cycle gradient slice (full + micro)
      Sidebar.tsx  SessionListItem.tsx
      StatusBar.tsx                     # waterline: status, import button, anomaly badge
      TabBar.tsx
      reader/
        ConversationView.tsx            # virtualized stream + header + band
        MessageTurn.tsx                 # eyebrow, accent border, blocks
        MarkdownProse.tsx               # react-markdown config (serif, code highlight)
        ToolBlock.tsx  ThinkingGlyph.tsx  SubagentChip.tsx  ImageBlock.tsx
      search/
        GlobalSearchTab.tsx  ConversationSearch.tsx  HitSnippet.tsx
    routes/
      SearchPage.tsx  SessionPage.tsx  SubagentPage.tsx
    lib/
      horizon.ts                        # pure slice math (background-size/position from times)
      urlState.ts                       # sidebar params helpers (fav, title)
      glow.ts                           # deep-link scroll + glow orchestration
  tests/  (mirrors src/: horizon.test.ts, Sidebar.test.tsx, urlState.test.ts, ...)
server/src/introspect/api/__init__.py   # + static mount of web/dist when present (Task 9)
```

Boundaries: `api/` knows fetch + types only; `components/` never fetch (data in via props/hooks from routes); `lib/` is pure functions (the testable math). No component imports another route's internals.

---

### Task 0: Server — expose the subagent join keys (Opus B1)

**Files:** Modify `server/src/introspect/api/models.py` (`BlockOut` + `tool_use_id: str | None`; `TranscriptInfo` + `parent_tool_use_id: str | None`), `server/src/introspect/api/routes/sessions.py` (`_message_out` and `get_session` populate them). Test: extend `server/tests/test_api_sessions.py`.

**Contract:** The DB carries both keys (`content_blocks.tool_use_id`, `Transcript.parent_tool_use_id`) but Phase 2 never exposed them — without them the UI cannot join a dispatch block to its subagent transcript (SubagentChip's core feature). Additive, backward-compatible fields. Tests: a fixture subagent's TranscriptInfo carries the parent_tool_use_id matching the dispatching block's tool_use_id in the main transcript's messages; existing 218 tests stay green.

- [ ] Steps: RED → implement → full python suite green + ruff → stage.

---

### Task 1: Scaffold + Still Water theme foundation

**Files:** `web/` project init (package.json, vite.config.ts w/ proxy + vitest config, tsconfig strict, eslint+prettier), `src/theme.css`, `src/main.tsx`, `src/App.tsx` (static shell grid: 300px sidebar / main / waterline footer), `index.html` (title "conversation-introspection", inline SVG favicon: a dragonfly-cyan dot on depth), `tests/smoke.test.tsx`.

**Contract:** `theme.css` transcribes the mockup's `:root` tokens + typography + reset EXACTLY (open the mockup file and copy its custom-property block; do not re-derive). Shell renders three themed regions with placeholder text. `npm test` green, `npm run lint` clean, `npm run build` succeeds. Vite proxy: `/api` → `http://127.0.0.1:8765`.
**`tests/setup.ts` (Opus M2 — mandatory, wired in vitest config):** jsdom lacks the measurement APIs react-virtuoso needs and matchMedia entirely. The setup file polyfills `ResizeObserver` (no-op class), stubs `HTMLElement.prototype.offsetHeight`/`scrollHeight` getters (fixed 800/24), and stubs `window.matchMedia` (query-matching mock overridable per-test). Without this, every virtualized-component test silently renders zero items.

- [ ] Steps: scaffold → theme transcription → shell → smoke test (renders three landmarks: nav/main/footer) → build+lint+test green → stage.

---

### Task 2: API types + client + query hooks

**Files:** `src/api/types.ts`, `src/api/client.ts`, `src/api/hooks.ts`. Test: `tests/api-client.test.ts` (mock fetch).

**Contract:**
- `types.ts` mirrors `server/src/introspect/api/models.py` + route response envelopes EXACTLY (SessionSummary, SessionDetail, TranscriptInfo, MessageOut, BlockOut, HitOut, Problem, ImportRun shapes, status/search/messages envelopes incl. `{items,total}`, `{groups,total}`, `{items,total,offset}`). Field names/optionality read from the Python source, not memory.
- `client.ts`: `apiFetch<T>(path, init?) -> Promise<T>`; non-2xx parses Problem JSON → throws `ApiError(status, title, detail)` (falls back to statusText when body isn't Problem-shaped). **204/empty bodies return undefined — never call res.json() unconditionally (favorites PUT/DELETE are bare 204s; Opus minor).** Test covers the 204 success path. All methods used by hooks: sessions list (params object → query string), session detail, messages (offset/limit/around), search, favorite put/delete, status, import trigger, import runs.
- `hooks.ts`: react-query hooks — `useSessions(filters)`, `useSession(uuid)`, `useMessages(transcriptId, {offset,limit,around})`, `useSearch(query, scope, sessionUuid)` (enabled only when query non-empty), `useStatus()` (refetchInterval 30s), `useFavorite()` mutation with sessions-list invalidation, `useTriggerImport()` mutation → returns run_id, `useImportRun(id)` (poll 1s while status==='running'). QueryClient defaults: no refetchOnWindowFocus, staleTime 30s (archive data changes slowly).
- Tests: ApiError thrown with Problem fields on 404; query-string building (filters incl. fav/title/project); useSearch disabled on empty query (renderHook).

- [ ] Steps: RED tests → implement → green+lint → stage.

---

### Task 3: Horizon band (the signature)

**Files:** `src/lib/horizon.ts`, `src/components/HorizonBand.tsx`. Test: `tests/horizon.test.ts` (the math), `tests/HorizonBand.test.tsx` (render).

**Contract:**
- `horizon.ts`: `sliceFor(startISO: string | null, endISO: string | null): {size: string, position: string} | null` — maps the session's LOCAL-time span onto the mockup's 24h day-cycle gradient. **The formula, explicit (Opus M1 — the mockup contains only computed outputs, not the derivation):** with `a` = start time as a fraction of the local day [0,1) and `d` = duration as a fraction of a day (clamped ≥ 1/48): `background-size = (100/d)%`, `background-position = (a/(1−d))·100%` (when d ≥ 1 → size 100%, position 0). Midnight crossers rely on the gradient's repeat-x wrap; the position formula handles them without special-casing. Gradient stops transcribed from the mockup CSS verbatim. Null/missing times → null (component renders nothing). Spans < 30min render a minimum visible slice (d floor 1/48). Times arrive as ISO-8601 UTC from the API; convert to LOCAL time for the day-cycle mapping (the band answers "when in MY day was this session" — document this choice in a NOTE).
- `HorizonBand.tsx`: `variant: 'full' | 'micro'` — full = 8px with right-aligned mono caption "HH:MM → HH:MM · Nh Nm" (crossing midnight shows the next-day time plainly); micro = 2px, NO caption, opacity 0.55 (the approved recession of the mockup's too-loud micro-bands).
- Tests: **fixture-anchored, not worker-computed (Opus M1):** the mockup's own 14:12→02:47 session must yield size 190.7% / position 124.4% (±0.1) with local time pinned via TZ-frozen Date inputs; its 22:00→01:30 micro-band → position 107.3%; null times → renders nothing; micro has no caption + reduced opacity; duration caption "12h 35m".

- [ ] Steps: RED (math first — pure function) → implement → component → green+lint → stage.

---

### Task 4: Sidebar

**Files:** `src/components/Sidebar.tsx`, `SessionListItem.tsx`, `src/lib/urlState.ts`. Test: `tests/Sidebar.test.tsx`, `tests/urlState.test.ts`.

**Contract:**
- Wordmark (serif italic "conversation-introspection"), title filter input (placeholder "Filter by title…", debounced 250ms → `useSessions`; syncs `?title=` via replace), All/★ Favorites toggle chips (`?fav=1`), session list from `useSessions` — item: title (ai_title ?? custom_title ?? uuid-prefix), project eyebrow (SessionSummary.project_slug tail, mono — the field is project_slug, NOT dir_slug which lives only on ProjectOut; Opus minor), date + message_count (mono), favorite star (dawn fill when favorited; click = mutation, optimistic), micro HorizonBand, active state (shore fill + 2px dragonfly left edge) from route match.
- Star click must not navigate (stopPropagation); item click → `/s/{uuid}` (preserving sidebar params). Empty states: no sessions ("Archive is empty — run introspect import"), no filter matches ("No conversations match"). Loading skeleton: 3 shimmerless placeholder rows (no animation — Still Water is calm).
- `urlState.ts`: pure param read/write helpers (fav, title) — tested directly.
- Tests: debounce (fake timers WITH `{shouldAdvanceTime: true}` — react-query's internal timers deadlock under plain fake timers (Opus minor); input 3 chars fast → ONE query call after 250ms); fav toggle updates URL + filters query; star click doesn't navigate; empty states render; active item matches route.

- [ ] Steps: RED → implement → green+lint → stage.

---

### Task 5: Conversation reader — stream + turns + prose

**Files:** `src/components/reader/ConversationView.tsx`, `MessageTurn.tsx`, `MarkdownProse.tsx`; `src/routes/SessionPage.tsx` wiring. Test: `tests/MessageTurn.test.tsx`, `tests/ConversationView.test.tsx`.

**Contract:**
- SessionPage: `useSession(uuid)` → header (serif 22px title, mono metadata row: uuid short, model from first assistant message if cheap else omit, message count, **and a mono "↓ .jsonl" link to `/api/v1/sessions/{uuid}/export.jsonl`** — three lines that surface the archive's headline capability; scope-reviewed addition), full HorizonBand, then ConversationView for the MAIN transcript (find via detail.transcripts kind='main').
- ConversationView — **bidirectional windowing model (Opus B2, binding):** state is `{firstItemIndex: number, items: MessageOut[]}`. Initial load without `around`: offset 0, firstItemIndex 0. With `initialAroundUuid` (prop accepted NOW, route-plumbed in Task 7): fetch with `around=` → seed `items = response.items`, `firstItemIndex = response.offset`, and virtuoso `initialTopMostItemIndex = targetIndexInPage` (find the target uuid in items). Virtuoso props: `totalCount = items.length` (**ANNOTATED post-review: the plan originally said response.total, which is incompatible with the growing-window prepend pattern specified in this same bullet — ghost rows + unreachable endReached; the shipped implementation is correct; archive total is held in state solely to gate append at the boundary**), `firstItemIndex` (this is what makes prepending scroll-stable), `startReached` → fetch `offset = max(0, firstItemIndex - 100)` with `limit = firstItemIndex - offset` (**ANNOTATED: exact gap, not flat 100 — around-seeds land at unaligned offsets; flat 100 duplicates rows and drives firstItemIndex negative**) → PREPEND items + decrement firstItemIndex by fetched count (no-op at 0), `endReached` → fetch `offset = firstItemIndex + items.length` → append. Page size 100. Each item = MessageTurn. Absolute↔array index mapping: absolute = firstItemIndex + arrayIndex — document with a NOTE.
- MessageTurn: full-width block, 2px left accent (dawn user / dragonfly assistant / mist system), eyebrow "SPEAKER · HH:MM" (mono 10px letterspaced; speaker labels: "YOU" for user, "CLAUDE" for assistant — generic by design, this repo is public and other users' archives aren't Donovan's; NOTE this choice in code), 28px spacing, blocks in block_index order dispatched by block_kind: text → MarkdownProse; others → Task 6 components (stub placeholders acceptable THIS task only if Task 6 lands after — check execution order: Task 6 is next; create minimal stubs with TODO(claude-task6) markers).
- MarkdownProse: react-markdown + remark-gfm + rehype-highlight; serif body per theme; code blocks mono on `--depth` background with overflow-x auto; links dragonfly; NO raw-HTML rendering (rehype-raw NOT included — untrusted content); highlight.js theme = a minimal Still-Water-tuned CSS (base16-ish on depth, no import of stock themes that fight the palette). **rehype-highlight MUST be configured to tolerate unknown/typo'd fence languages (transcripts carry ```jsonl, ```mermaid, arbitrary tags) — verify the installed version's unknown-language behavior and configure accordingly (ignoreMissing or subset+fallback); a bad fence must never throw (Opus M3). Test: a ```notalang fence renders as a plain code block without throwing.**
- Tests: user vs assistant accent classes + blocks render in block_index order (in `MessageTurn.test.tsx` — un-virtualized, jsdom-honest); ConversationView windowing tested at the logic level (setup.ts mocks active): around-seed sets firstItemIndex from response offset; startReached prepends and decrements; endReached appends (mock the API hook, assert state transitions); markdown renders bold/code; **HTML never mounts: `container.querySelector('script') === null` for markdown containing raw `<script>` (Opus minor — react-markdown drops html nodes; asserting rendered-as-text is version-fragile).**

- [ ] Steps: RED → implement → green+lint → stage.

---

### Task 6: Block renderers — tool, thinking, subagent, image

**Files:** `src/components/reader/ToolBlock.tsx`, `ThinkingGlyph.tsx`, `SubagentChip.tsx`, `ImageBlock.tsx`; replace Task 5 stubs in MessageTurn. Test: `tests/blocks.test.tsx`.

**Contract:**
- ToolBlock (tool_use + tool_result): collapsed row — chevron, mono label `⌘ {tool_name}` (tool_use) or `→ result` (+ ember accent when is_error), byte-size hint when text_content > 2KB; click expands to mono pre (overflow-x auto, max-height 400px scroll). Collapsed by default ALWAYS.
- ThinkingGlyph: ◌ dragonfly at 55% opacity, `title` + aria-label "thinking occurred — content not persisted by the CLI". Renders for block_kind='thinking' regardless of empty text (the honest marker).
- SubagentChip: for tool_use blocks whose tool_name is the dispatch tool (detect: payload/tool_name === 'Task' or an agent-ish name — inspect real data shape via BlockOut.tool_name; document what you keyed on): pill `⑂ subagent · {description from payload input if present}` + "view transcript →" → navigates `/s/{uuid}/a/{agentHex}` — BUT the agent hex comes from the transcripts inventory matched by parent_tool_use_id === block.tool_use_id (SessionDetail.transcripts is already loaded; provide it via a React context created in SessionPage — prop-drilling through the virtualized MessageTurn tree is the wrong shape). Unmatched (no transcript captured) → chip renders without link + title "transcript not captured".
- ImageBlock (block_kind='image'): renders nothing but a mono `[image]` chip v1 — payload base64 decode deferred (note in code; the archive stores it, the room needn't show it yet). YAGNI.
- Tests: tool collapsed by default + expands; error result gets ember class; thinking glyph has the aria-label; subagent chip links when transcript matched, degrades when not; oversized content shows size hint.

- [ ] Steps: RED → implement (kill Task 5 stubs) → green+lint → stage.

---

### Task 7: Tabs, searches, URL sync, deep-link glow

**Files:** `src/components/TabBar.tsx`, `search/GlobalSearchTab.tsx`, `ConversationSearch.tsx`, `HitSnippet.tsx`, `src/lib/glow.ts`, `src/routes/SearchPage.tsx`; SessionPage + ConversationView integration (`around` plumbing). Test: `tests/search.test.tsx`, `tests/glow.test.ts`.

**Contract:**
- TabBar: "Search all conversations" | "Current conversation" — active = dragonfly underline + aria-selected. Tab state derives from ROUTE (/search vs /s/*), not local state.
- SearchPage (/search?q=): input, Enter commits → pushes `?q=`; renders `useSearch(q,'global')` groups — session header (serif, clickable → session), hits as HitSnippet (mono badge block_kind, snippet with `<mark>` rendered via sanitized transform — snippets are API-generated with only `<mark>` tags: render by SPLITTING on the literal mark tags into React elements; NEVER dangerouslySetInnerHTML), timestamp; has_more per group → "more in this conversation →" linking to `/s/{uuid}?q=`.
- ConversationSearch (on SessionPage): input Enter → `/s/{uuid}?q=term` (push); when `?q=` present, show flat `useSearch(q,'session',uuid)` results ABOVE a "back to conversation" affordance (clears q); clicking a hit → `/s/{uuid}/m/{record_uuid}?q=term` (keeps q).
- Deep link /s/{uuid}/m/{msgUuid}: SessionPage passes `initialAroundUuid` → ConversationView calls useMessages with `around`, virtuoso scrolls to the target index, then `glow.ts` applies the dawn glow (2s fade via CSS class; removed after; `prefers-reduced-motion` → scroll only, class never applied). Target inside a collapsed tool block: v1 targets are messages (uuid-level), so N/A — glow the whole MessageTurn.
- Empty q on /search: calm empty state ("Search every archived conversation"), not 422 round-trips (client guards).
- Tests: Enter commits + URL updates (user-event); mark-splitting renders <mark> as styled element and never injects (snippet containing `<script>` stays text); glow class applied then removed (fake timers) and NOT applied under reduced-motion (matchMedia mock); tab active state follows route.

- [ ] Steps: RED → implement → green+lint → stage.

---

### Task 8: Subagent drill-in + status bar

**Files:** `src/routes/SubagentPage.tsx`, `src/components/StatusBar.tsx`; App wiring. Test: `tests/SubagentPage.test.tsx`, `tests/StatusBar.test.tsx`.

**Contract:**
- SubagentPage (/s/{uuid}/a/{agentHex}): resolves transcript id from SessionDetail.transcripts (agent_hex_id match; unknown → inline not-found state w/ link back), breadcrumb "← back to conversation" (preserves nothing but uuid), header shows agent_type + description (mono), then ConversationView on that transcript — messages fetched ONLY here (lazy contract honored). Deep-link /m/{msgUuid} works here too (same around plumbing).
- StatusBar (waterline, all routes): from `useStatus()` — "last import {relative} · {records} records · {files} files" (mono 11px), center: "● import" ghost button → `useTriggerImport()`; while running (poll via useImportRun) shows "importing…"; 409 → brief "already running" text (no toast library — a 4s inline message); right: anomaly badge "{n} anomalies" (ember only when error>0, mist otherwise) + "archive: {MB} MB". Failures render "archive offline" calmly (server down ≠ blank bar).
- Tests: unknown agentHex → not-found state; lazy fetch (messages hook NOT called until page mounts — spy); import button → mutation + polling state text; 409 path shows already-running; offline status renders fallback.

- [ ] Steps: RED → implement → green+lint → stage.

---

### Task 9: Production serving + build integration

**Files:** Modify `server/src/introspect/api/__init__.py` (mount `web/dist` static + SPA fallback), `server/src/introspect/cli.py` (serve: log whether UI is present), `web/package.json` (build script), README (serve section gains "open http://127.0.0.1:8765"). Test: `server/tests/test_api_static.py`.

**Contract:**
- create_app: UI dist resolution (Opus M4 — never Path.cwd()): `INTROSPECT_UI_DIST` env/param first; else walk up from `Path(__file__).resolve()` to the directory containing `web/dist/index.html` (repo checkout); site-packages installs without the env var → API-only with a logged note. Mount order (Opus minor, binding): API routers FIRST; static assets mounted at `/assets`; the SPA catch-all (`/{path:path}` GET → index.html) registered LAST and re-raising the 404 problem for any path starting with `api/` (a catch-all otherwise swallows unmatched /api/v1/* into index.html). `/api/*` NEVER falls back — regression test hits unknown `/api/v1/zzz` and asserts problem-JSON. No dist → API-only (current behavior, 218 tests still green).
- Python tests: with a tmp fake dist (index.html + asset), `/` serves index, `/s/whatever` serves index (SPA fallback), `/assets/x.js` serves the asset, `/api/v1/health` still JSON, unknown `/api/v1/zzz` still problem-404. Without dist: current suite green (218 stay green).
- README: Quickstart gains the two-line UI story (`cd web && npm install && npm run build`, then `introspect serve` → open browser).

- [ ] Steps: RED (python tests) → implement → FULL python suite (218+) + web tests green → stage.

---

### Task 10: Live verification — walking the room (controller-run)

- [ ] `cd web && npm run build` → `uv run introspect serve` (background) → Playwright against `http://127.0.0.1:8765`:
  - Sidebar renders real sessions, this session at top; micro-bands receded; title filter narrows as-you-type; favorite star round-trips.
  - Open this session: horizon band spans real hours with caption; serif prose; tool blocks collapsed; thinking glyphs present; markdown code highlighted.
  - Search "horizon band" globally → groups render with marks → click a hit → deep-link lands + glow → refresh URL reproduces view.
  - Drill into a real subagent transcript (lazy fetch visible in network); breadcrumb back.
  - Status bar live numbers; trigger import from the UI; watch it complete.
  - Screenshots at each beat (the mirror check, on the real room). Record results + screenshots noted in `claude_notes/`; kill server.

---

## Execution notes for the orchestrator

- Order: 0 (server, sonnet, single review) → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 sequential (each consumes the prior's components; web tasks share config files — no parallel dispatch). 9 touches server/ only (could parallel 8, but keep sequential — cheap). 10 controller-run last.
- Model calibration: opus for 3 (the signature math), 5, 6, 7 (reader + interaction core); sonnet for 1, 2, 4, 8, 9.
- Review calibration: full two-stage review 5, 6, 7; single review 2, 3, 4, 8, 9; controller spot-check 1.
- Node/npm: verify availability at Task 1; if npm missing, STOP and surface (don't improvise installs beyond `brew install node` with a note).
- Commit checkpoints (authored Claude): after 4 (shell+sidebar), after 8 (app complete), after 10 (verified).
- Every task: `npm test` + `npm run lint` + `npm run build` green before staging (build catches TS strict errors tests miss). Server tasks additionally keep the python suite green.
