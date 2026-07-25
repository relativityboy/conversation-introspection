# Phase 4: Publication Readiness — Implementation Plan (revision 2)

> **Revision 1:** Opus critique FIX-THEN-SHIP — 9 findings (2 blockers, 3 majors, 4 minors), all accepted, zero vetoed, folded in. Fourth consecutive all-real critique.
> **Revision 2 (relativityboy's in-session rulings, 2026-07-19):** snippets BATCHED (`best_snippets`, one query per request); attachments-IN confirmed; ZERO-LEGACY — the single `project=` param, the `title=` alias, and the legacy `?title=` client read are all removed, not deprecated. Spec §8/§14.1 amended to match.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Git rule:** relativityboy owns pushes; the controller commits at checkpoints (authored as Claude). Workers only `git add` — never commit.

**Goal:** The four features that make the reading room publishable (spec §14): sidebar content+uuid search with snippet hints, the project-filter subsystem (app-level chip bar, relativityboy's keyboard spec verbatim), editable session titles (user-data layer), and a sticky conversation-only mode.

**Spec authority:** `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` **§14** — final, all critique rulings baked. Where this plan quotes a §14 resolution it is restating, not re-deciding. Phase 1–3 API/UI shapes are binding contracts — read them from source (`server/src/introspect/api/models.py`, routes, `web/src/api/types.ts`), never from memory.

**Current-state anchors (verified 2026-07-19 against the working tree):**
- `GET /sessions` = `list_sessions` (`api/routes/sessions.py` ~L125): params `title/favorite/project/limit/offset`; title filter is lowercased LIKE over `ai_title|custom_title`; ordering is the THREE-key contract `(last_activity_at.is_(None), last_activity_at.desc(), session_uuid)` — the module docstring rejects `nullslast()` deliberately.
- `SearchIndex` Protocol lives in `search/fts5.py` (~L69) with `index_blocks/delete_for_blocks/delete_all/search/rebuild`; DI is the module singleton `get_search_index()`; `sanitize_query` same file; ALL raw FTS SQL is confined there today. Keep it that way — **binding (§14.1 critique #1): zero raw FTS SQL in routes, ever.**
- `GET /transcripts/{id}/messages` = `list_messages` (sessions.py ~L194): `total` count (~L208), `around` ordinal count (~L219), page fetch (~L230) — **none type-filtered today**; ordering is `Message.id` alone (load-bearing for `around`).
- Favorites = the user-data pattern to copy: `Favorite` model (models.py ~L199), PUT/DELETE 204 idempotent routes, and `test_favorite_survives_import_and_reparse`. NOTE: `reparse_all` RESETS `sessions.ai_title/custom_title` caches (`_reset_session_caches`) — archive titles are reparse-owned; the new user title must live apart and survive.
- Alembic revisions are sequential strings (`'0001'`,`'0002'`); next is **`'0003'`**; 0002 created FTS5 objects via raw `exec_driver_sql` + explicit backfill with a frozen predicate cross-checked by `test_index_predicate_matches_migration_backfill`.
- Web: `urlState.ts` handles only `title`/`fav` (`q` is ad-hoc in SearchPage/ConversationSearch; no project param exists). App grid is `'nav main'/'footer footer'` — no top row yet. ConversationView's three fetch sites: `useMessages` seed (~L49), `loadBefore` direct `fetchMessages` (~L144), `loadAfter` direct `fetchMessages` (~L165); MessageStream remount key is `` `${transcriptId}:${around ?? ''}` `` on the EFFECTIVE around (the "view from the beginning" recovery drops it). `fetchProjects`/`ProjectOut` exist unused; NO localStorage anywhere yet; `<mark>` splitting lives in `HitSnippet.renderSnippet` (module-private today). Session header is inline JSX in `SessionPage.tsx` (h1 ~L68); SubagentPage shares ConversationView with its own inline header.

## Resolved ambiguities (spec-fidelity ledger — every gap named, nothing silent)

1. **Attachments in chat_only — `type IN ('user','assistant','attachment')`.** §14.4's headline ruling (relativityboy 2026-07-19: "pasted things are things a human said") governs over the stale `('user','assistant')` bullet below it. **CONFIRMED by relativityboy in-session 2026-07-19.**
2. **Snippets are BATCHED — relativityboy ruling 2026-07-19 (supersedes the critiqued per-session interpretation).** The Protocol method is `best_snippets(db, session_uuids: list[str], q) -> dict[str, str]`: ONE call per request returning all of the page's content-matched snippets in one query. "Keep DB query count down if we can get the same quality results." Spec §14.1 amended to match.
3. **The single `project=` param is REMOVED — relativityboy ruling 2026-07-19: "We haven't released. Zero code debt, zero legacy stuff."** `projects=` (comma list) is the only project filter; the old param, its filter code, its test, and the client's unused `SessionFilters.project` all go.
4. **No `title=` alias — same zero-legacy ruling applied (controller extension, one word reverses it).** `q=` replaces `title=` outright on `GET /sessions`; the client writes/reads only `?filter=`, no legacy `?title=` read. The alias's only beneficiaries would have been our own pre-release bookmarks. Spec §14.1 amended.
5. **uuid-substring match is case-insensitive** (lower both sides; stored uuids are lowercase hex).
6. **The chat-only toggle control renders in BOTH readers' headers** (SessionPage and SubagentPage) — §14.4 says the mode "applies in main and subagent readers" and the control "lives in the conversation header"; both readers have one.
7. **Subagent chips disappear under conversation-only.** They render from `tool_use` blocks, which full-prose mode hides. Intended per §14.4's "full prose mode"; named so nobody "fixes" it.
8. **`match_snippet` is null whenever title or uuid matched** — populated only for content-only matches (§14.1 verbatim: "matched by content and not by title").
9. **Unknown slugs in `projects=`** filter to nothing server-side (no error); the chip renders the raw slug client-side (§14.2 critique #11).

## Global constraints

- **SearchIndex Protocol boundary (binding, protects §13 Postgres):** all new FTS SQL lives in `search/fts5.py` behind the Protocol. Routes compose Protocol calls only.
- **List contract:** every sessions-list query path — filtered, unioned, project-scoped — preserves the three-key ordering exactly. The snippet is a hint, never a re-rank (§14.1 critique #2).
- **User-data invariant:** `user_titles` follows the favorites family — never touched by import/reparse (tested identically), structurally excluded from export (export reads raw bytes only), never merged into the reparse-owned `sessions.*title` caches.
- **relativityboy's project-filter keyboard spec is verbatim-binding** (§14.2): down-arrow-when-empty opens the alphabetized list; typing filters dir_slug `%str%`; double-esc (two Escape keydowns within **400ms**): list-open-or-text-present → clear+close, else → remove ALL chips; select → chip right of box + clear box; chip 'x' removes; zero chips → "all projects" chip.
- **Everything-in-the-URL** rule continues: `projects=slug1,slug2` comma list on sidebar, `/search`, `/s/*`; sidebar param renamed `?filter=` (client reads legacy `?title=`, writes strip it).
- **chat_only windowing correctness:** the server computes total/ordinal/page/target within the filtered set; the client threads `chat_only` through ALL THREE fetch sites and the remount key (§14.4 critique #3).
- TS strict, eslint+prettier clean, ruff clean, type hints on public functions. RED-first per task; the full suite (`server: uv run pytest`, `web: npm test`) green + linters at every task close. Stage-only workflow (git add; write-tree snapshots for review diffs).
- Localhost tool, no auth, no external network at runtime — unchanged.

## New/renamed files

```
server/alembic/versions/0003_user_titles.py
server/src/introspect/api/routes/titles.py
server/tests/test_migration_0003.py  test_api_titles.py
web/src/components/ProjectFilterBar.tsx
web/src/components/reader/ChatOnlyToggle.tsx      # shared control, both readers
web/src/components/TitleEditor.tsx
web/src/lib/chatOnly.ts                           # useChatOnly (localStorage introspect.chatOnly.v1)
web/src/lib/snippet.tsx                           # renderSnippet lifted from HitSnippet (shared with sidebar hints)
web/tests/ProjectFilterBar.test.tsx  TitleEditor.test.tsx  chatOnly.test.ts(x)  SessionListItem hint cases in Sidebar.test.tsx
```

---

### Task 1: Server — `user_titles` (migration 0003 + model + title API + invariant)

**Files:** `alembic/versions/0003_user_titles.py`; `models.py` (+`UserTitle`); `api/routes/titles.py` (+ router registration); `api/models.py` (`SessionSummary.user_title: str | None = None` — `SessionDetail` inherits); `sessions.py` `_summary()`/`get_session` populate it (LEFT JOIN or correlated subquery, mirroring `_is_favorited`); **`search.py` — `_summary()` has a THIRD caller: `_session_summary` (~L123) building `SearchGroup.session`. Thread `user_title` at ALL THREE call sites (sessions.py ~L164, ~L187, search.py ~L123), else search group headers never carry the user title and §14.3's "precedence everywhere" breaks in search results (critique F1, blocker).** Tests: `test_migration_0003.py` (mirror 0002's binding-contract style: table/PK/FK exist, downgrade drops), `test_api_titles.py` (mirror the favorites suite + a search-group-header-carries-user_title case).

**Contract:** Table `user_titles(session_uuid TEXT PK FK→sessions, title TEXT NOT NULL, updated_at UTCDateTime)`. **Migration DDL convention (critique F7): datetime columns are `sa.String()` in migrations** — `UTCDateTime.impl = String` and 0001 pins this deliberately ("decoupled from app code"; also the §13 dialect-aware invariant). `updated_at` → `sa.String()`, `session_uuid` → `sa.String()` FK. No preflights needed (0002's were FTS5/partial-index-specific). `PUT /api/v1/sessions/{uuid}/title {title: str}` → 204: upsert (second PUT updates title + updated_at); **empty/whitespace title deletes the row** (revert to archive titles; delete-when-absent still 204); unknown session → 404 problem; `len(title) > 200` → 422 problem (§14.3 critique #10; cap applies post-strip decision: validate on the raw string, strip only for the empty-check). Revision `'0003'`, `down_revision='0002'`, sequential-string convention. The invariant test **`test_user_title_survives_import_and_reparse`** mirrors the favorites one (PUT → `run_import` + `reparse_all` → row + `user_title` in API responses intact — this also proves it survives `_reset_session_caches`).

**Named risks for review:** reparse exclusion (grep `_delete_all_interpretation_rows`/`_reset_session_caches` — `UserTitle` must appear in neither); export untouched (raw-bytes path — verify no schema import creep); 422 problem shape matches existing `_problem` helpers.

- [ ] RED (migration + API + invariant tests) → implement → full suite + ruff → stage.

---

### Task 2: Server — SearchIndex Protocol extension (§14.1/§14.2 core)

**Files:** `search/fts5.py` (Protocol + `Fts5SearchIndex`), `search/__init__.py` re-exports if needed. Tests: `test_search_fts5.py` (+ `test_search_integration.py` if wiring warrants).

**Contract (§14.1 critique #1 as amended 2026-07-19, binding signatures):**
- `session_uuids_matching(db, q, project_slugs: list[str] | None) -> list[str]` — the one corpus-wide FTS pass; returns distinct session uuids whose text content matches sanitized `q`, optionally constrained to projects.
- `best_snippets(db, session_uuids: list[str], q) -> dict[str, str]` — **BATCHED (ledger #2, relativityboy ruling)**: one query returning each listed session's best-bm25 snippet (`<mark>`s included); sessions with no match are absent from the dict. Empty input list → empty dict, zero queries.
- `search(db, query, *, session_uuid=None, project_slugs: list[str] | None = None, limit, offset)` — existing signature gains `project_slugs` (global-scope filtering for `/search`).
All three sanitize via the existing `sanitize_query` (empty sanitized → empty results, never raises — extend `test_sanitize_never_raises` family). Join path for project constraint: `content_fts → content_blocks → messages → transcripts → sessions → projects.dir_slug` — SQL stays in this module's template constants (extend the `{session_filter}` slot pattern).

**Named risks for review:** per-session best-rank selection INSIDE the batched query (each session's snippet must be its bm25-best match, not its first-rowid match — a bare GROUP BY picks arbitrarily; use a min-rank window/subquery and TEST a fixture where a session's best match is not its first); project filter must not break the external-content table's rowid join; empty `project_slugs` list vs None semantics (None = unfiltered; `[]` = matches nothing — document + test both); Protocol and impl signatures stay identical (a Protocol drift here breaks the Postgres promise silently). **Scale ceiling, acknowledged not fixed (critique F8):** `session_uuids_matching` materializes the full match set into a Python list consumed via an expanding `IN (...)` — older SQLite builds cap bound variables at 999. Fine at v1 scale; NOTE(claude) the ceiling in code so the Postgres/scale pass knows where to look. (The F8 snippet fan-out concern is MOOT — batching was ruled.)

- [ ] RED → implement → full suite + ruff → stage.

---

### Task 3: Server — `GET /sessions` content search (`q=`, `match_snippet`)

**Files:** `sessions.py` `list_sessions`; `api/models.py` (`SessionSummary.match_snippet: str | null = None`). Tests: extend `test_api_sessions.py`.

**Contract (§14.1 as amended):** New `q=` param **replaces `title=` outright — remove the old param and its filter block (ledger #4, zero-legacy ruling); the existing `test_sessions_title_filter_matches_ai_and_custom_case_insensitive` is REWRITTEN against `q=` (coverage kept, param gone)**. Match = OR of: (a) case-insensitive `session_uuid` substring; (b) lowercased LIKE over **user_title | ai_title | custom_title** (user_titles LEFT JOIN — a user rename must not shadow an archive-title match, critique #5); (c) `session_uuid IN session_uuids_matching(db, q, project_slugs=<from projects= when Task 4 lands, else None>)`. **Ordering: the union keeps the three-key contract unchanged** — one SELECT with the OR predicate, no re-rank (critique #2). `match_snippet`: ONE `best_snippets()` call per request for the page items that matched by content and NOT by title/uuid (ledger #2 batched + #8); null otherwise. Empty/absent `q` → unfiltered list (existing behavior).

**Tests (RED):** `title=` param no longer accepted (unknown-param behavior consistent with FastAPI defaults — assert it no longer filters); uuid-substring (mixed case); user-title match; archive-title match not shadowed by a user rename; content-only match populates `match_snippet` with `<mark>`; title-match leaves it null; ordering preserved when union mixes title- and content-matched sessions; remaining sessions tests green.

**Match-attribution requirement (critique F9):** the OR-predicate SELECT doesn't reveal WHICH disjunct matched, so populating `match_snippet` "only when matched by content and not by title/uuid" requires a per-page-row re-check in Python: uuid-substring, title LIKE (user/ai/custom, replicating the SQL's `lower()` + `%`/`_` escaping EXACTLY), then content-set membership. The Python and SQL matching semantics must stay byte-identical — factor the needle-building into one shared helper so they cannot drift.

**Named risks for review:** LIKE-escape of `%`/`_` in `q` (the P2 ledger already flags the existing title path — do not make it worse; escaping both is in scope); N-snippets bounded by page size (ledger #2, ceilings noted in T2); no raw FTS SQL enters the route; the Python/SQL match-attribution helper stays single-sourced.

- [ ] RED → implement → full suite + ruff → stage.

---

### Task 4: Server — `projects=` multi-value filter

**Files:** `sessions.py` (`list_sessions`), `search.py` (`search`). Tests: extend `test_api_sessions.py`, `test_api_search.py`.

**Contract (§14.2 + ledger #3):** `projects=` accepts a comma list of dir_slugs on `GET /sessions` and `GET /search`. **The single `project=` param is REMOVED — param, filter block, and `test_sessions_project_filter` (rewritten against `projects=`, including a two-slug case). Zero-legacy ruling.** Sessions: filter `Project.dir_slug IN (...)`; composes with `q=` (both the LIKE/uuid predicate AND `session_uuids_matching(project_slugs=...)` receive it). Search global scope: pass `project_slugs` into `search()`. **Session scope: the route explicitly IGNORES `projects=` (critique #7) — accepted-and-ignored, with a test proving a session-scope search on an out-of-filter session still returns hits.** Unknown slugs match nothing, no error (ledger #9).

- [ ] RED → implement → full suite + ruff → stage.

---

### Task 5: Server — `chat_only=` message filtering

**Files:** `sessions.py` `list_messages`. Tests: extend `test_api_sessions.py`.

**Contract (§14.4 + ledger #1):** `chat_only: bool = False` query param. When true, the predicate `Message.type.in_(("user","assistant","attachment"))` applies to **all four** query sites: the `total` count (~L208), the `around` ordinal count (~L219), the page fetch (~L230), **and the around-target resolution** — so a deep-linked system message 404s within the filtered set (the existing unknown-uuid 404 path fires; §14.4 edge). `offset` echo remains the effective offset within the filtered set. `Message.id`-alone ordering unchanged.

**Tests (RED):** filtered totals + paging; `around` centers correctly within the filtered set (fixture with system rows interleaved — the analog of `test_around_centers_mid_target_and_clamps_early_target`); system-message `around` target → 404 problem under `chat_only=1`, found without it; default (absent param) behavior byte-for-byte unchanged (existing windowing tests green).

- [ ] RED → implement → full suite + ruff → stage.

---

### Task 6: Web — API types/client/hooks extensions (all four features)

**Files:** `api/types.ts` (`SessionSummary.user_title/match_snippet`; `SessionFilters` gains `q`/`projects` and **DROPS the unused `title`/`project` fields — zero-legacy ruling**; `MessagesOptions.chat_only`), `api/client.ts` (`putSessionTitle(uuid, title)` → PUT, resolves 204/undefined; `fetchSessions`/`fetchMessages`/`fetchSearch` param serialization — `projects` joins with commas; `chat_only` serializes as `1`/absent), `api/hooks.ts` (`useProjects()` over the existing orphan `fetchProjects`; `useSessionTitle()` mutation invalidating **`['sessions']` (prefix covers both the list and the `['sessions', uuid]` detail key — that IS the detail key, hooks.ts ~L38, not `['session', uuid]`) AND `['search']` (search groups embed `SessionSummary` titles under `searchKey` — without this a renamed session shows its stale title in search results until staleTime lapses; critique F2/F6)**; `useMessages` passes `chat_only` inside opts — the query key `['messages', transcriptId, opts]` absorbs it automatically). Tests: `api-client.test.ts`.

**Contract:** Types mirror the Phase 4 server schemas exactly (read the Python source). `title: ''` on `putSessionTitle` is the documented revert path (client sends it verbatim; server deletes). No component changes in this task.

- [ ] RED → implement → suite + lint → stage.

---

### Task 7: Web — sidebar content search + `?filter=` rename

**Files:** `lib/urlState.ts` (+tests: rename `title`→`filter` key; **no legacy `?title=` read — zero-legacy ruling, ledger #4**), `Sidebar.tsx` (debounced value feeds `useSessions({ q })`), `SessionListItem.tsx` (mist one-line snippet hint under the title when `match_snippet` present — slot between title div and project eyebrow), `lib/snippet.tsx` (lift `renderSnippet` out of `HitSnippet.tsx`; HitSnippet imports it — no behavior change there). Tests: `urlState.test.ts` (11 existing — mechanical churn, budgeted per §14 execution note), `Sidebar.test.tsx` (10 existing + hint render + legacy-param read), `search.test.tsx` stays green (renderSnippet lift is refactor-neutral).

**Contract (§14.1):** As-you-type debounce (250ms) unchanged; the single input now searches title OR content OR uuid via server `q=`. Hint styling: mist, single line, ellipsized; `<mark>` renders via the shared splitter (dawn ink, consistent with search tab). Sidebar continues never re-ranking — order arrives from the server.

**Named risks for review:** the `lastWrittenTitle` echo-guard survives the rename (the debounce regression tests exist — keep them meaningful, not just renamed); a deep-link with `?filter=` seeds the input on load.

- [ ] RED → implement → suite + lint → stage.

---

### Task 8: Web — ProjectFilterBar component (relativityboy's keyboard spec, verbatim)

**Files:** `components/ProjectFilterBar.tsx`, `App.tsx` + `App.css` (new grid row: areas `'topbar topbar' / 'nav main' / 'footer footer'`), `lib/urlState.ts` (+`readProjects`/`writeProjects` — comma list ↔ `string[]`). Tests: `ProjectFilterBar.test.tsx` (the task's center of gravity), `urlState.test.ts` additions.

**Contract (§14.2, verbatim-binding):** Thin app-level bar above sidebar+main. Default: chip "all projects" with 'x'. 'x' → SearchBox replaces it. Focused+empty+ArrowDown → alphabetized full project list (from `useProjects`); typing → list filtered dir_slug `%str%` (case-insensitive substring); Enter/click selects → chip appended right of box, box cleared, list stays usable; chip 'x' removes that chip; zero chips → revert to "all projects" chip. Selected chips render dir_slug; unknown/stale slugs from the URL render raw (ledger #9). State source of truth: the `projects=` URL param (read on mount, written on every change — replace, not push).

**The Escape machine (critique F3 — this exact design, binding):** §14.2 defines only the DOUBLE-tap, branching on the state AT THE GESTURE. A single esc must therefore never clear text or touch chips (doing either makes the spec's first branch unreachable by two real keypresses — the state would already be empty/closed when the second esc lands, silently nuking chips). Design: on every Escape keydown, capture `(timestamp, stateBeforeThisPress)` in refs. If `now - lastEscAt ≤ 400ms` → double-tap: branch on the state captured at the FIRST press of the pair — list-open-or-text-present → clear text + close list; else → remove ALL chips. Single esc (no pair): close the list only — never clears text, never touches chips.

**Named risks for review:** the 400ms window is a ref-timestamp comparison, not a timer race (fake-timer tests inside and outside the window); **the two mandatory F3 regression tests: (type text → double-esc → text cleared, chips SURVIVE) and (empty box, closed list → double-esc → chips removed)**; the pre-gesture-state capture survives the first press's list-close; list keyboard nav (ArrowDown/ArrowUp/Enter) + click both select; focus returns to the box after chip add/remove; aria: listbox/option roles + `aria-expanded`.

- [ ] RED (keyboard-spec test matrix first — every clause above is a test) → implement → suite + lint → stage.

---

### Task 9: Web — project filter plumbing (everything inherits the context)

**Files:** `Sidebar.tsx` (`useSessions` gains `projects`), `routes/SearchPage.tsx` (global search passes `projects`; URL preserved), `SessionListItem.tsx` + any `Link`/`NavLink` builders (deep links carry `projects=` — sidebar items, search hit links, group headers, subagent links, TabBar tab switches), `ConversationSearch.tsx` (session-scope: does NOT pass projects — server ignores anyway; client stays clean). Tests: extend `Sidebar.test.tsx`, `search.test.tsx`, router-level assertions in `SubagentPage.test.tsx`/`ConversationView.test.tsx` where links are built.

**Contract (§14.2):** All existing deep links carry the filter param; sidebar re-queries live as chips change; the §14.1 content search respects the filter (server-side via Task 3/4 — client just passes it). Removing the last chip restores unfiltered everything.

**Named risks for review:** link-building is scattered — grep for `createSearchParams`/template-built `to=` strings and enumerate every site in the report; params survive the SearchPage↔SessionPage tab switch round-trip.

- [ ] RED → implement → suite + lint → stage.

---

### Task 10: Web — inline title editor

**Files:** `components/TitleEditor.tsx`, `routes/SessionPage.tsx` (h1 → editor), title-precedence sweep: `SessionListItem.tsx` (~L42 fallback chain), `SessionPage.tsx` (~L42), search `GroupHeader`/group session title render — **precedence everywhere: `user_title > ai_title > custom_title > uuid-prefix`** (§14.3). Tests: `TitleEditor.test.tsx`, precedence cases in `Sidebar.test.tsx`/`search.test.tsx`.

**Contract (§14.3):** Click the session-header title → input (pre-filled with the current display title). **Enter commits; single Escape cancels the edit; double-Escape (two ≤400ms) clears the user title entirely** (sends `title: ''` → revert to archive titles); blur commits-if-changed. A small mist "edited" dot marks renamed sessions; its `title=` attribute shows the archive's original (`ai_title ?? custom_title ?? uuid-prefix`). 422 (>200 chars) surfaces as an inline problem-title message, edit stays open. Mutation invalidates list + detail so the sidebar re-titles live.

**The esc mechanism (critique F5 — this exact design, binding):** "first esc cancels, second upgrades to clear" cannot live inside the input alone — a closed editor's keydown handler is unmounted and can't hear the second press. Design: first esc closes the editor immediately (instant cancel feel) AND installs a document-level keydown listener that self-removes after 400ms; a second Escape arriving through it fires the clear-title mutation (`title: ''`). Both outcomes close the editor; the upgrade deletes the user title. (Deliberately a DIFFERENT machine from Task 8's — there, nothing closes on first press; here, close-then-listen. Do not share an implementation.) Fake-timer tests: esc→esc at 399ms clears; at 401ms doesn't; the listener never survives past its window; esc→click-elsewhere→esc does not clear.

**Named risks for review:** blur-vs-Enter double-commit guard; the document listener is removed on unmount (navigation away during the 400ms window); the editor never renders for the uuid-prefix fallback as if it were a real archive title (editing starts from '' in that case, not the uuid).

- [ ] RED → implement → suite + lint → stage.

---

### Task 11: Web — conversation-only toggle (THE trap task — §14.4 critique #3)

**Files:** `lib/chatOnly.ts` (`useChatOnly()`: localStorage `introspect.chatOnly.v1`, greenfield — no storage helper exists), `components/reader/ChatOnlyToggle.tsx` (mist styling), `routes/SessionPage.tsx` + `routes/SubagentPage.tsx` (toggle in both headers — ledger #6; header keeps unfiltered `message_count` and appends mist "· conversation only" while active — NO second server count, critique #6), `components/reader/ConversationView.tsx` (thread + notice), `components/reader/MessageTurn.tsx` (block-level hiding). Tests: `chatOnly.test.ts`, `ConversationView.test.tsx` additions, `MessageTurn.test.tsx` additions, `SubagentPage.test.tsx`.

**State model (critique F4 — blocker fix, binding):** ONE owner per reader page. `SessionPage` / `SubagentPage` each call `useChatOnly()` EXACTLY ONCE, render the toggle from it, and thread `chatOnly` + `setChatOnly` as PROPS into `ConversationView` (which passes `chatOnly` down to `MessageStream` → `MessageTurn`, and hands `setChatOnly` to the 404-notice's "show all message types" action). Components never call `useChatOnly()` independently — parallel useState-from-localStorage instances do not sync; the header would toggle while the reader silently never re-seeds (the exact seam bug the walk exists to catch). `useChatOnly` = useState seeded from localStorage + a setter that writes localStorage; no cross-tab sync requirement.

**Contract (§14.4):** `chat_only` threads through **ALL THREE fetch sites** — the `useMessages` seed (opts object → query key absorbs it), `loadBefore`'s direct `fetchMessages`, `loadAfter`'s direct `fetchMessages` — and the MessageStream remount key becomes `` `${transcriptId}:${around ?? ''}:${chatOnly ? 1 : 0}` `` so toggling re-seeds the window cleanly (unfiltered edge pages against a filtered window corrupt the offset math). Block-level: when active, `MessageTurn` hides `tool_use`/`tool_result` blocks (subagent chips disappear — ledger #7, intended), keeps `text`/`thinking`/`image`. **The 404 notice gains the second action (critique #12): "show all message types" (sets chatOnly false — keeps the same `around=` seed) alongside the existing "view from the beginning" (keeps the toggle, drops `around`).** Sticky across sessions and readers via the one localStorage key; default off.

**Named risks for review:** the remount key uses the EFFECTIVE around (post-recovery) — preserve that semantics; toggling while an edge fetch is in flight (pendingRef) must not splice a stale page into the new window (the remount unmounts MessageStream — verify the guard's lifecycle); localStorage unavailable (private mode) degrades to in-memory state without throwing; the "show all message types" action must re-resolve a previously-404'd around target successfully (test the full recovery sequence); **a toggle-sync test that toggles via the HEADER and asserts the reader body re-seeds (the F4 regression).**

- [ ] RED (the three-sites threading test matrix first) → implement → suite + lint → stage.

---

### Task 12: Docs refresh

**Files:** `README.md` (Phase 4 features in the feature list + mini-manual), `docs/` user/dev pages if present for search/reader behavior. Small, sonnet-grade; the P2 final review caught README staleness — do not repeat it.

- [ ] Update → stage.

---

### Task 13: Final whole-branch review (fable) → fix wave

Write-tree diff of the full phase vs `TREE_PHASE4_START`; fable reviewer with the concrete named risks accumulated in the ledger + per-task minors; fix wave to still-warm implementers via SendMessage; controller verifies closures. Include the standing dispatch inoculation line; adjudicate any security claim against primary sources before remediating (project history: 4/4 false positives).

- [ ] Review → fix wave → full suites + linters green → stage.

---

### Task 14: The walk (controller, in the browser, MANDATORY)

Against the production archive: sidebar content search (uuid fragment, a user-title, a content phrase with snippet hint), project filter (chips, keyboard spec by hand incl. double-esc both tiers, deep-link carry), title edit (rename, revert via double-esc, edited dot), conversation-only (toggle in main + subagent readers, deep-link 404 → both recovery actions, window scroll integrity at edges), plus the Phase 1–3 regression walk (search → subagent drill-in → export). Screenshots to repo root. The Phase 3 walk caught a cross-layer routing bug 349 unit tests missed — walks catch seam bugs; never skip.

- [ ] Walk → fix anything caught → re-walk failing paths → report.

---

## Execution notes

- **Dependencies:** T1→T3 (user-title LIKE); T2→T3,T4; T5 independent; T6 after T1/T3/T4/T5 freeze the API shapes; T7 needs T3+T6; T8 needs T6; T9 needs T8; T10 needs T1+T6; T11 needs T5+T6. T2 can run parallel to T1; T5 parallel to T1–T4; web tasks sequence T6 → {T7, T8} → {T9, T10, T11}.
- **Model guidance:** judgment-dense (T2, T3, T8, T11) → opus implementers + full review; pattern-copy/mechanical (T1, T4, T5, T6, T7, T9, T10, T12) → sonnet implementers with named-risk briefs; review depth calibrated per task risk as ever. Name CONCRETE risks in reviewer briefs — the ledger items above are the seed list.
- **Budget note (§14 execution):** the `?title=`→`?filter=` rename churn (11 urlState tests + Sidebar assertions) is budgeted inside T7 — mechanical, not scope creep.
- **NOT Phase 4** (do not drift): ¶ copy-link anchors, drift-floor schema loop (info anomalies), ghost recovery (§13), push-to-public decision (relativityboy's).
