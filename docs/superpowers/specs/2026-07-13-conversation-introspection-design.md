# conversation-introspection — Design

**Date:** 2026-07-13 · **Status:** draft for relativityboy's review (sections 1–5 discussed live; 6–13 are Claude's take, pending review)
**One-liner:** A local-first, fully independent archive + reading room for Claude Code session transcripts. The filesystem is an ephemeral feed; our database is the system of record.

## 1. Purpose & core reframe

Claude Code writes session transcripts as append-only `.jsonl` under `~/.claude/projects/`, and the TUI **actively deletes older sessions**. Current history is already incomplete. Therefore this is **not a cache — it is an archive**:

- Capture is lossless (byte-faithful raw lines) and add-only; source deletion never propagates.
- The app works with zero source files present. Fully independent.
- We can reconstruct a byte-faithful `.jsonl` for any captured session at any time.
- User-generated data (favorites) is never derivable from sources and never touched by import/reparse.

## 2. Decisions already made (with relativityboy, 2026-07-13)

| Decision | Choice |
|---|---|
| Stack | Python 3.12+ / FastAPI / SQLAlchemy / Alembic / Pydantic v2; React + TypeScript + Vite front-end |
| Database | SQLite (WAL) now; Postgres later — nothing SQLite-flavored above the storage layer |
| Search index | FTS5 behind a `SearchIndex` interface (tsvector implementation at the Postgres jump) |
| Search scope | User + assistant **text** blocks. Thinking-block search **designed but deferred**: verified across 1,400+ blocks and every CLI version 2.1.12x→2.1.202 that thinking text is never persisted (empty string + signature only). Schema stays thinking-ready via `block_kind`; the UI toggle ships when data exists. |
| Subagents | Captured always; UI is drill-in (option b) with **lazy loading** — transcript fetched only when the user drills in |
| Ingestion topology | Option A: importer as library + CLI entry point (cron-registrable) + `POST /api/import` invoking the same code; advisory lock; idempotent via byte-offset checkpoints |
| Schema philosophy | Strict at interpretation, unconditional at capture. Versioned Pydantic schema registry; every record stores `detected_cli_version` + `parsed_with_schema_version`; deviations recorded in `parse_anomalies`, never block capture |
| Normalization | 3NF where it pays; deliberate read-path exceptions flagged (title cache on `sessions`) |

## 3. Source-data facts (survey, 2026-07-13)

144 files / ~47 MB today; median file 144 KB, max 4.7 MB; single lines up to ~528 KB; inline base64 images; `file-history-snapshot` records embed file backups. 12+ record types: conversational (`user`, `assistant`, `system`, `attachment`) share a stable envelope (uuid/parentUuid/sessionId/timestamp/cwd — stable across all observed versions); thin meta records (`ai-title`, `custom-title`, `mode`, `permission-mode`, `last-prompt`, `queue-operation`, `agent-name`, `file-history-snapshot`) may lack uuid/message entirely. Subagent transcripts are separate files at `<projectDir>/<sessionId>/subagents/agent-<hex>.jsonl` with a sibling `agent-<hex>.meta.json` (`{agentType, description, toolUseId}`) linking to the dispatching `tool_use` block. No compaction/summary records exist. Message-internals drift across versions (fields added/dropped); envelope does not.

## 4. Data model

Four layers: identity → archive → interpretation → user data. Interpretation is always rebuildable from archive; user data never is, so it lives apart.

**Identity**
- `projects` — id, dir_slug, resolved_cwd (from envelopes, not slug-decoding), first_seen_at
- `sessions` — session_uuid PK, project_id FK, started_at, last_activity_at, ai_title, custom_title (latest-wins caches, flagged denormalization)
- `transcripts` — id, session_id FK, kind (`main`|`subagent`), agent_hex_id, agent_type, agent_description, parent_tool_use_id. (Source files point *at* transcripts, not vice versa — one transcript may have several: primary + backup + divergence generations.)

**Archive (system of record)**
- `source_files` — id, project_id FK, transcript_id FK, path, kind (`main`|`subagent`|`backup`), is_primary (the file reconstruction reads from), byte_offset_checkpoint, last_size, prefix_hash (hash of the ingested prefix, recomputed as the checkpoint advances — divergence detector), status (`active`|`gone_at_source`|`diverged`), first/last_seen_at, gone_detected_at
- `raw_records` — id, source_file_id FK, transcript_id FK (denormalized for query paths, always derivable via source_file), line_number (per source file), byte_offset, raw_line (exact bytes), record_type, record_uuid (nullable), detected_cli_version, parsed_with_schema_version, parse_status (`ok`|`partial`|`anomaly`), ingested_at. **Reconstruction = `SELECT raw_line WHERE source_file = <primary> ORDER BY line_number`.**
- `import_runs` — id, trigger (`cli`|`api`), started/finished_at, files_seen, records_added, anomaly_count, status
- `parse_anomalies` — id, raw_record_id FK (nullable for file-level events), source_file_id FK, severity (`info`|`warn`|`error`), kind, detail JSON, schema_version, created_at

**Interpretation (rebuildable via `reparse`)**
- `messages` — raw_record_id 1:1, record_uuid, parent_uuid, transcript_id FK, timestamp, type, model, cwd, git_branch, request_id
- `content_blocks` — message_id FK, block_index, block_kind (`text`|`thinking`|`tool_use`|`tool_result`|`image`|…), text_content, tool_name, tool_use_id, is_error, payload JSON
- `token_usage` — message_id 1:1 (assistant only), input/output/cache columns
- `session_events` — raw_record_id 1:1, event_kind, payload JSON (thin meta records; titles also update `sessions`)
- `file-history-snapshot` records: captured in `raw_records`, deliberately **not interpreted**; embedded blobs never indexed
- FTS5 external-content table over `content_blocks.text_content` (no double storage), behind `SearchIndex`

**User data**
- `favorites` — session_uuid FK, created_at (own table so rebuilds can't lose it; message-level favorites later = additive table)

## 5. Schema registry & versioning

- Pydantic v2 models per record type, grouped in a registry with **our own version** (`introspect-schema/1`), independent of CLI versions. The registry maps record `type` → model; models validate at import and serialize the API (one source of truth).
- Tolerance tiers, all captured regardless: unknown extra fields → `info` anomaly (normal forward drift); unknown record type → `warn`; malformed envelope on known type / unparseable JSON → `error`.
- `reparse` CLI re-runs interpretation from stored raw lines (no source files needed — they may be gone), bumping `parsed_with_schema_version`. Anomaly counts before/after are the drift-fix feedback loop.

## 6. Ingestion pipeline

1. **Discover.** Walk `~/.claude/projects/*/`: main transcripts at project root (`<uuid>.jsonl`), subagents under `<sessionId>/subagents/*.jsonl`, plus `*.jsonl.bak-*` (captured as kind=`backup`, tied to the same session; reconstruction defaults to the primary file). Read `agent-*.meta.json` for transcript linkage; tolerate its absence.
2. **Diff against checkpoints.** New file → full ingest. `size > checkpoint` → tail ingest from checkpoint; a partial trailing line (no newline yet) is left for the next run — checkpoint only advances past complete lines. `size < checkpoint` or prefix-hash mismatch → **divergence**: `error` anomaly, freeze existing rows, re-ingest file as a new generation of `source_files` (lossless both ways; this path is speculative armor — append-only is the observed norm).
3. **Missing at source** → status `gone_at_source` + timestamp. Rows untouched.
4. **Per line:** capture raw → dispatch on `type` → interpret through the registry → write interpretation rows + FTS. Chunked transactions (~500 records) so a 4.7 MB file doesn't hold one giant transaction. Stream line-by-line; never slurp (528 KB lines exist).
5. **Restored-source dedup.** relativityboy keeps out-of-band backups of transcripts (late-April onward); a restored backup arrives as the same session at a *different path*. Record-level identity makes that safe: within a transcript, an incoming line whose record_uuid + raw-byte hash already exist **in a different source file** is skipped (counted, not re-stored) — same-file matches are never skipped, since dropping a line from a file breaks its byte-faithful reconstruction (final-review finding, 2026-07-14); same record_uuid with *different* bytes → `error` anomaly, never a silent overwrite. Thin records without uuids dedup on (transcript, raw-byte hash, line_number). Consequence: "import from backup" is just import — no special mode.
6. **Concurrency:** advisory file lock (second runner exits 0 with "already running"); SQLite WAL + busy_timeout so reads never block during import.
7. **CLI** (console scripts): `introspect import` · `introspect reparse` · `introspect export <session-uuid> [-o file]` · `introspect status`. Exit codes: 0 clean, 1 completed-with-error-anomalies, 2 fatal. Summary line to stdout, detail to stderr; every run recorded in `import_runs`. Cron example: `*/15 * * * * /path/to/venv/bin/introspect import`.
8. Out of scope v1 (see §13): `~/.claude/history.jsonl`, `sessions-index.json`.

## 7. Search

- `SearchIndex` interface: `index(blocks)`, `delete_for_records(ids)` (reparse support), `search(query, scope, session_uuid?, limit, offset)` → hits `(session_uuid, transcript_id, message_uuid, block_index, block_kind, snippet, rank)`.
- FTS5 implementation: external-content table, bm25 ranking, `snippet()` for excerpts; user query sanitized into FTS5 `MATCH` syntax (bare quotes/operators escaped; quoted phrases supported).
- **Title filter (sidebar, as-you-type):** plain `LIKE` over `sessions.ai_title/custom_title` — hundreds of rows, no index machinery warranted.
- **Content search (main area, on Enter):** cross-session scope groups hits by session (session header + snippets); within-session scope returns a flat hit list. Only `block_kind='text'` indexed in v1 (see §2).
- Postgres path: second `SearchIndex` implementation on tsvector; interface, API, and UI unchanged.

## 8. API surface (`/api/v1`, JSON; server binds 127.0.0.1 only)

- `GET /projects`
- `GET /sessions?q=&favorite=&projects=&limit=&offset=` — date desc; list-item shape includes titles, times, counts, favorite flag (§14: `q=` replaced `title=`; `projects=` comma-list replaced the single `project=` — zero-legacy ruling 2026-07-19)
- `GET /sessions/{uuid}` — detail incl. transcript inventory (subagent metadata only — no messages)
- `GET /transcripts/{id}/messages?offset=&limit=&around=<message_uuid>` — paged for virtualization; `around` centers a page on a deep-linked message; serves main and subagent transcripts uniformly (lazy drill-in)
- `GET /search?q=&scope=global|session&session=&projects=&limit=&offset=` (§14.2: `projects=` comma-list filters global scope; session scope accepts-and-ignores it)
- `PUT /sessions/{uuid}/favorite` · `DELETE /sessions/{uuid}/favorite`
- `POST /import` → 202 + run id (409 if lock held) · `GET /import/runs?limit=` · `GET /import/runs/{id}`
- `GET /sessions/{uuid}/export.jsonl` — reconstruction download (same bytes as CLI export)
- `GET /status` — record/session counts, archive size, last import, anomaly summary
- `GET /anomalies?severity=&limit=&offset=`
- Errors: problem-details JSON (`{status, title, detail}`). No auth (localhost tool).

## 9. Web UI

**Layout (per relativityboy's spec):** left sidebar — as-you-type title filter, favorites toggle, conversation list date-desc; main area — tab 1: all-content search (Enter-committed, URL-synced), tab 2: selected conversation with within-conversation search (Enter-committed, URL-synced). Added: bottom status bar (last import, counts, anomaly badge, import trigger button).

**URL scheme (React Router; all state shareable):**
- `/search?q=term` — global search tab
- `/s/{sessionUuid}` — conversation · `/s/{uuid}?q=term` — within-conversation search
- `/s/{uuid}/m/{messageUuid}` — deep link to message (fetch `around=`, scroll, glow-highlight)
- `/s/{uuid}/a/{agentHexId}[/m/{messageUuid}]` — subagent drill-in
- Sidebar state as params (`?fav=1&title=…`)

**Conversation rendering:** no chat bubbles — full-width blocks, 2px left voice-accent border, eyebrow (speaker · time), serif prose with rendered markdown + syntax-highlighted code. Tool use/result blocks collapsed by default (mono, size-aware; images decoded on demand). Thinking = marker glyph ("thinking occurred — content not persisted by the CLI"). Subagent dispatch renders a chip → drill-in route (lazy). Message lists virtualized (sessions reach thousands of records); deep-link arrival auto-expands any collapsed block containing the target (relevant today for messages, load-bearing later if tool-content indexing lands).

**Theme — "Still Water"** (Claude's stamp, mockup at `docs/design/2026-07-13-still-water-mockup.html`): blue-hour palette (`--depth #0B1220`, `--surface #111B2E`, text `#E7ECF4`), voice accents dawn amber `#E5A54B` (relativityboy) / dragonfly cyan `#6FD3C7` (Claude), ember `#D4756B` for anomalies only. Serif conversation prose (Charter stack), mono for terminal material, sans chrome. Signature: the **horizon band** — an 8px gradient strip atop each conversation mapping its real time-span onto a day-cycle gradient (sessions crossing midnight/sunrise show it), echoed as a 2px micro-band per sidebar item. Motion limited to deep-link glow; reduced-motion respected. Dark-only v1.

## 10. Error handling & independence guarantees

- UI never 500s because sources are missing, locked, or weird — it reads only the DB.
- Import failures are per-line or per-file, recorded as anomalies; one bad file never halts the run (fatal = can't open DB, nothing else).
- Capture-before-interpret ordering means a crash mid-import loses no captured data; checkpoints advance only after commit.
- Reconstruction is byte-faithful including trailing-newline state.
- DB file default `~/.conversation-introspection/archive.db` (outside any repo working tree — archives don't live where `git clean` can reach them); overridable via `INTROSPECT_DB` / `--db`. Source root overridable likewise (tests point at fixtures).

## 11. Testing (TDD; red/green)

- **Flagship: round-trip test.** Import fixture tree → `export` → byte-compare to source. This single test carries the archive guarantee.
- Schema registry vs fixture lines per record type per version era. **Fixtures are synthetic** — real transcripts contain private content and never enter the repo.
- Importer: idempotency (run twice → zero new rows), tail-append, partial-trailing-line, divergence, source-deletion independence, lock contention.
- Anomaly tiers: unknown field → info; unknown type → warn; malformed envelope → error; all with raw captured.
- Search: FTS round-trip, query sanitization, scope filtering. API: FastAPI TestClient over a fixture DB.
- Front-end: Vitest + RTL for logic-bearing components (URL-sync of searches, debounced title filter, tab state); Playwright smoke deferred until there's a running app to smoke.

## 12. Repo layout & tooling

```
server/    pyproject.toml (uv), src/introspect/{schema,ingest,search,api,export}/, alembic/, tests/
web/       Vite + React + TS, eslint + prettier, src/{components,routes,api}/, tests/
docs/      superpowers/specs/, design/ (mockup lives here)
claude_notes/  claude_tasks/
```
Dev: uvicorn + Vite dev server (proxy `/api`); FastAPI serves `web/dist` for the single-process daily-driver mode. Ruff + black-compatible formatting; type hints on public functions; Alembic from migration 0001 (FTS5 virtual tables via raw-SQL migrations).

## 13. Future work (explicit, decided-not-drifted)

- **Postgres path.** `SearchIndex` gains a tsvector implementation; Alembic migrations written dialect-aware from day one (no SQLite-only DDL outside the FTS migration, which gets a Postgres twin); JSON columns already portable (SQLAlchemy `JSON`). Unchanged: schema registry, API, UI, importer logic, reconstruction guarantee.
- **`history.jsonl` ghost recovery (v2).** Measured 2026-07-13: 195 sessions in prompt history since 2026-01-19; only 12 transcripts survive (TUI ~30-day retention); 183 "ghost" sessions with 4,059 prompts have history.jsonl as their only trace. Deferred to v2 by decision (relativityboy lean + Claude concurrence): the file is backed up and unpruned, so deferral loses nothing, while v1 scope stays on the time-sensitive work — capturing living transcripts. v2 shape when it comes: source kind `prompt_history`, its own schema model, `ghost` status on `sessions`, tombstone rendering (your-voice-only timeline, first-prompt title fallback). Note: relativityboy's out-of-band transcript backups (late-April onward) mean many May "ghosts" are actually recoverable in full via restored-source import (§6.5) — permanent losses concentrate Jan–Apr.
- **Thinking-block search + UI toggle.** Designed (§2), ships if the CLI ever persists thinking text.
- **Export upgrade mode (added 2026-07-20, relativityboy's intent).** The original byte-faithful export stays the default and remains unchanged forever — an exact reconstruction of the transcript as the CLI wrote it. A future optional flag may additionally export a transcript brought forward to an arbitrary newer schema version (re-serialized and clearly labeled as non-original, never masquerading as the captured bytes), motivated by richer future memory-experience tooling that wants a normalized, current-generation view.

## Review log

- 2026-07-13: relativityboy approved cron cadence (15 min), `.bak` capture, DB home `~/.conversation-introspection/`; `history.jsonl` deferred to v2 (rationale above). Sections 6–13 reviewed live in session.

## 14. Phase 4 — Publication readiness (added 2026-07-19, relativityboy's feature set + Claude's resolutions)

Four features that make the reading room publishable. relativityboy's UI specifications are binding where given verbatim below; Claude's design resolutions were reviewed and approved in-session. **Execution: a fresh instance** (this addendum + plan written by the 5-day session; build belongs to full headroom). Orientation for that instance includes walking the built room.

### 14.1 Sidebar content search
The sidebar's single input matches **title OR chat content** (was: title only). As-you-type (debounced 250ms) stays.
- Server: `GET /sessions` gains `q=` (**replaces `title=` outright — no deprecated alias; amended 2026-07-19, relativityboy's zero-legacy ruling: pre-release, zero code debt — the old param and its filter code are removed**). Matching: **session_uuid substring** (the uuid-esque string — relativityboy addition 2026-07-19) OR title LIKE **as three ORs over user/ai/custom titles (find it wherever it lives — a user rename must not shadow an archive-title match; critique #5 resolution)** OR FTS content match. **Ordering: the union keeps the list contract `last_activity_at DESC NULLS LAST` — the snippet is a hint, never a re-rank (critique #2 resolution).** Response items gain `match_snippet: str | null` — populated (best-rank snippet, `<mark>`s included) only when the session matched by content and not by title.
- **SearchIndex Protocol extension (critique #1 — binding; protects the §13 Postgres promise):** ALL new FTS SQL stays behind the interface. Protocol gains `session_uuids_matching(db, q, project_slugs: list[str] | None) -> list[str]` and **`best_snippets(db, session_uuids: list[str], q) -> dict[str, str]` (BATCHED — amended 2026-07-19, relativityboy ruling: one call returning all of the page's snippets; keep DB query count down)**; `search()` gains `project_slugs: list[str] | None = None`. Routes compose these — zero raw FTS SQL in `sessions.py`/`search.py`. Snippet strategy: one FTS pass per request, never a query-per-session-per-keystroke.
- UI: content-matched sessions show a one-line mist snippet hint under the title (mark-splitting renderer reused). Sidebar URL param renamed `?filter=` (**no legacy `?title=` read — zero-legacy ruling 2026-07-19**).

### 14.2 Project filter (the subsystem)
Scope everything by a chosen subset of projects. **relativityboy's UI spec, binding verbatim:**
- Thin top bar (app level, above sidebar + main — spans both, since its context scopes both) showing a chip "all projects" with an 'x'.
- 'x' removes it and replaces with a SearchBox. SearchBox focused: down-arrow when empty → alphabetized select-list of projects; typing → select-list filtered on dir_slug `%str%`; **double-tap `<esc>`**: if select-list open OR text present → clear text + close list; else (list closed, box empty) → remove ALL selected project chips & filtering.
- Selecting a project in the list → adds a project chip to the right of the search box, clears the box, **and closes the list (amended 2026-07-20, relativityboy walk ruling — UI tablestakes)**. Chip 'x' removes that chip. Zero chips selected → bar reverts to the "all projects" chip.
- **List dismissal (amended 2026-07-20, same ruling):** the list also closes on input blur / click-outside — guarded so an option mousedown never races the blur into a swallowed click. Escape behavior unchanged (single esc closes list; double-esc per the branch spec above).
Semantics:
- Both search tabs (`Search all conversations`, `Current conversation`) inherit the filter context. All existing deep links carry it (URL param `projects=slug1,slug2` — comma list, everywhere: sidebar, /search, /s/*; consistent with the everything-in-the-URL rule).
- As filters change, the session list re-queries live (and the sidebar content search of §14.1 respects the filter).
- Server: `projects=` multi-value on `GET /sessions` and `GET /search` (global scope; via the Protocol's `project_slugs` param). **Session-scope search: the route explicitly IGNORES `projects=` (critique #7 — threading it through would filter out the very session being read; "accepted, harmless" means accepted-and-ignored, tested).**
- Unknown/stale slug in `projects=`: chip renders the raw slug (critique #11). Double-esc window: two Escape keydowns within **400ms** (critique #8).

### 14.3 Editable session titles (user data)
- **Data:** new `user_titles` table — `session_uuid (PK, FK), title (TEXT), updated_at (UTCDateTime)`. User-data layer: never touched by import/reparse (favorites-family invariant, tested identically). Structurally excluded from export (export reads raw bytes only).
- **API:** `PUT /api/v1/sessions/{uuid}/title {title: str}` → 204; empty/whitespace title → deletes the row (revert to archive titles). 404 problem unknown session. `SessionSummary`/`SessionDetail` gain `user_title: str | null`.
- **Display + search precedence everywhere:** `user_title > ai_title > custom_title > uuid-prefix`. The §14.1 title LIKE match includes user_titles. "IS searchable" is satisfied via the title path (user titles are not injected into content FTS).
- Title length: cap **200 chars**, server 422 problem on overflow (critique #10).
- **UI (Claude's approved choice): inline** — click the session-header title → becomes an input; Enter commits, **single esc cancels the edit (convention), double-esc (400ms) clears the user title entirely (revert to archive titles)**, blur commits-if-changed; a small mist "edited" dot marks renamed sessions (title attribute shows the archive's original).

### 14.4 Conversation-only toggle
One sticky toggle ("conversation only") hiding non-chat material. **Scope (approved): full prose mode** — hides system-type message rows AND tool_use/tool_result blocks inside kept messages; keeps prose and thinking glyphs. **Attachments: IN — `type IN ('user','assistant','attachment')` (relativityboy ruling 2026-07-19: pasted things are things a human said).**

*(2026-08-04: conversation-only additionally trims content-empty rows — see docs/superpowers/specs/2026-08-04-conversation-view-refinements-design.md §4.)*
- Message-row filtering is **server-side** (windowing correctness: totals/offsets/around must be computed within the filtered set): `GET /transcripts/{id}/messages` gains `chat_only=1` → `type IN ('user','assistant','attachment')` (bullet aligned to the attachments-IN ruling, 2026-07-19 — was stale pre-ruling text; caught by the T5 reviewer).
- Block-level hiding (tool blocks within assistant messages) is client-side presentation (no pagination impact).
- Sticky via localStorage key `introspect.chatOnly.v1`; applies in main and subagent readers. Toggle lives in the conversation header, mist styling. Header keeps the unfiltered `message_count` and appends mist "· conversation only" while active (no second server count — critique #6 resolution).
- **Client plumbing (critique #3 — the implementer trap, binding):** `chat_only` threads through ALL THREE fetch sites in ConversationView (the `useMessages` seed AND both direct `fetchMessages` edge loaders), into the react-query key, and into the `MessageStream` remount key (`${transcriptId}:${around}:${chatOnly}`) so toggling re-seeds the window cleanly. Unfiltered edge pages against a filtered window corrupt the offset math — the plan must name all three sites.
- Edge (spec'd): a deep-link `around=` target that is a system message while `chat_only=1` → server 404s within the filtered set; the not-found notice then offers BOTH actions with distinct semantics (critique #12): "show all message types" (disables the toggle, re-seeds with the same `around=`) and the existing "view from the beginning" (keeps the toggle, offset 0).

## 15. Post-4 refinements (added 2026-07-20, relativityboy's list; two already shipped as §14.3/§14.4)

### 15.1 Archived sessions
User-data table `archived_sessions(session_uuid PK/FK, created_at)` (favorites-family: import/reparse never touch it; capture/sync of the session CONTINUES — only read is prevented). ALL API read paths exclude archived sessions: `GET /sessions` (list+detail 404), `/search` (hits filtered — behind the SearchIndex boundary or post-filter at the route; no raw FTS in routes), `/transcripts/{id}/messages` (404 when the owning session is archived), `/sessions/{uuid}/export.jsonl` (404). Discovery requires direct DB query — by design. Archive action: `PUT /sessions/{uuid}/archive` → 204 (idempotent) + a quiet affordance in the session header; the session vanishes from the UI on success. **Unarchive: CLI ONLY — `introspect unarchive <session-uuid>` (uuid must be known; no list command reveals archived sessions; resolution pending relativityboy veto).** Status counts may show an aggregate archived count (n only, no identities).

### 15.2 Raw-record inspector
Per-message expand affordance (mono `{}` button in the row gutter/eyebrow) → modal over the reader showing the record's exact `raw_line` (server: `GET /records/{record_uuid}/raw` → the stored bytes; render pretty-printed JSON with a raw-bytes toggle; byte-faithful source, never re-serialized for the raw view). Navigation: ◀/▶ buttons + Left/Right hotkeys for previous/next record **in the parent reader's current traversal** — inherits conversation-only filtering when active, with an in-modal enable/disable toggle for that filter. Esc closes. Reduced-motion respected; focus trapped in modal, returned on close.

*(2026-08-04: the inspector's trigger is the speaker name, not a `{}` glyph, and pretty JSON is colorized — refinements spec §6/§7. The Phase-4 "¶ anchor" backlog item is superseded by the timestamp deeplink, refinements spec §5.)*

## 16. TUI — the base interface (added 2026-07-20, relativityboy's spec; step 1 of ≥2)

`introspect tui` (bare `introspect` unchanged — cron safety). Framework: **Textual** (new dep, justified: pure-Python, no native deps, actively maintained, the de-facto Python TUI standard; pulls rich). Primary surface: a type-with-autocomplete command input.

- **Slash commands** (registry pattern — step 2 adds more cheaply): `/help` (commands + one-liners), `/help <command>` (long description + examples + caveats), and every CLI verb replicated: `/import`, `/reparse`, `/export <uuid> [path]`, `/status`, `/unarchive <uuid>`. Long-running verbs stream progress lines into the TUI log area (import/reparse run in a worker, UI stays live).
- **Web server management, in-process uvicorn:** `/web start` (127.0.0.1:8765), `/web start public` (binds 0.0.0.0 — **prints a mandatory warning: no auth, entire archive readable by anyone on the network**), `/web stop`, bare `/web` = status (the /cron shape). `/status` includes web-server state + url:port. TUI must refuse `/web start` when the port is already bound by another process (clear message, no crash). (Amended 2026-08-11 from `/start-web`/`/stop-web` — zero-legacy rename, no aliases. Same amendment: URLs in the TUI log are interactive — click copies via pbcopy, and the span is an OSC 8 hyperlink so supporting terminals open it on cmd+click. Also added 2026-08-11: `/changelog [all]` — newest release entry bare, full history with `all`, same best-effort degradation as the version banner; and a resizable results/log split — draggable 1-row divider between the panels plus alt+up/alt+down and ctrl+shift+up/down bindings, log clamped ≥3 rows, results ≥5; bare arrows deliberately unbound — results-nav today, command history reserved. Added 2026-08-16: `/skill [install | status]` — repo-shipped agent skills under `skills/<name>/SKILL.md` are templates (`__INTROSPECT_SERVER_DIR__` placeholder) rendered for the local checkout and installed into `~/.claude/skills/`; status compares rendered-vs-installed bytes; the one sanctioned write outside the archive.)
- **Default input (no slash) = archive search**, in-process via the SearchIndex/session layer (works with web server stopped): arrow-navigable result list — session title (display precedence), project, date, best-hit snippet. **Enter → open browser at `/s/{uuid}`; Right-arrow → open browser at `/s/{uuid}/m/{best-hit record_uuid}`** (auto-starts the web server on 127.0.0.1 if stopped — a browser launch needs a server; noted in the action's help). Archived sessions excluded (§15.1 applies to TUI search — it is a read path).
- Esc clears input; Ctrl-C / `/quit` exits (stopping any web server it started). Still Water palette approximated in terminal colors.
- **Amended 2026-07-20 (relativityboy):** Enter on a result opens the browser at *the specific best-hit message* (`/s/{uuid}[/a/{hex}]/m/{record}`), same as Right — one destination, two keys.

### §9 amendments (2026-07-20, relativityboy; Claude's judgment ratified in-session)
- **Deep-linked message: persistent highlight.** The `/m/{uuid}` target keeps a subtle persistent marker (dawn-tinted left accent + faint wash) after the arrival glow fades; URL is read at page load only, never rewritten on scroll (existing behavior, now stated).
- **Input contrast.** Search/filter inputs get higher-contrast treatment (visible border, moonpaper text, brighter placeholder) WITHIN Still Water — tokens unchanged; the mockup's input styles updated to match (theme.css covenant: mockup first). No theming system — contrast is repair, not variety.

### Execution notes
Same arc: plan (fresh instance writes it against this addendum) → Opus critique → SDD with per-task reviews → final review → **walk** (mandatory — Phase 3's walk caught what tests couldn't). Plan-authoring budget notes (critique #9): the `?title=`→`?filter=` rename touches urlState.ts helpers, all 10 urlState tests, and Sidebar test assertions — mechanical churn, budget it. Backlog explicitly NOT in Phase 4: per-message ¶ copy-link anchor (mockup-only gap), drift-floor schema loop (24→27), ghost recovery (§13), push-to-public decision (relativityboy's).

## 17. Resume links (added 2026-07-24, relativityboy's feature; design approved in-session)

**Purpose: lower the bar to continuing a conversation.** relativityboy's motivating cases: a promise to come back, an understanding a past instance had, a crash that ate the session sha. One click next to the session title → a terminal sitting in the session's original directory with `claude` resumed into it. The archive stops being only a record and becomes a door.

### 17.1 Server — `resume.py` (cron.py architecture: pure logic + injectable subprocess edge)
`resume_session(db, session_uuid, source_root, terminal_app, runner) -> ResumeOutcome`, in order:
1. **Presence:** live path = `source_root/<project.dir_slug>/<session_uuid>.jsonl`; plain `exists()`.
2. **Restore if missing** via existing `export.export_session_to()` (byte-faithful), `mkdir -p` the slug dir first (fully-deleted project folders are a real case). **Presence gates the write — an existing live file is NEVER touched** (no diffing, no overwrite; the live file may be ahead of the archive).
3. **Directory:** `Project.resolved_cwd` (envelope-parsed, per §4 — **never slug-decoding**). Null or no-longer-on-disk → degrade per 17.3; deleted project dirs are NOT recreated.
4. **Launch script** written to `<db_dir>/resume-scripts/<uuid>.command` (overwritten per click, `chmod +x`): shebang **`#!/bin/zsh -il`** (login — `claude` resolves against the USER's PATH, not the server's; the script IS the 4a/4b fallback, running in the only environment where launchability is true; interactive — `.zshrc` is sourced, so direnv/autoenv-style hooks fire on the script's `cd` and the project's `.env`/`.envrc` loads exactly as in a hand-opened terminal. Amended 2026-08-11 from `-l`: without `-i`, MCP servers whose tokens come from project-root `.env` woke dead in resumed sessions): `cd <quoted cwd>`, then `command -v claude` → `exec claude --resume <uuid>`, else `pbcopy` the resume command + echo what happened. `cwd` shlex-quoted; uuid comes from the DB row.
5. **Launch:** `runner(["open", "-a", terminal_app, script])` — LaunchServices, **no AppleScript, no TCC Automation prompt**. Config `config.terminal_app()`: explicit arg > `INTROSPECT_TERMINAL_APP` env > `"Terminal"` (the §10 `INTROSPECT_DB`/`--db` precedence pattern), resolved at serve-time onto `app.state`.

### 17.2 Endpoint
`POST /api/v1/sessions/{uuid}/resume` (new `routes/resume.py`; **POST — spawns a terminal per click, not idempotent**) → 200 `ResumeResult {restored, launched, mode, command, cwd, live_path}`. HTTP errors only when we can't even try: 404 unknown, **404 archived** (amended at self-review from an approved 409: §15.1's discovery-prevention is binding — archived sessions are indistinguishable from nonexistent on every API path, export-endpoint precedent; UI hides the button on archived sessions anyway; CLI-only unarchive ruling stands). Launch failures after a successful restore are **outcomes, not exceptions** — always 200 + honest `mode`. CSRF posture (wording corrected at final review): the guards are the unguessable session-UUID capability plus the 127.0.0.1 default bind — not preflight, which a simple cross-origin form POST never triggers; docs note the 0.0.0.0 implication (remote clients could pop terminals on the host).

### 17.3 Degradation ladder
`mode` encodes the LAUNCH outcome only; `restored` is orthogonal (the UI status message composes the two — e.g. "restored from archive · launched"). Modes: `launched` (happy) · `missing_cwd` (no launch; command + missing path returned, command selectable in UI) · `open_failed` (`open` nonzero — likely bad `INTROSPECT_TERMINAL_APP`; stderr + command returned) · `unsupported_platform` (non-darwin: restore still performed, command returned) · claude-not-on-PATH handled INSIDE the script (pbcopy + message in the already-open terminal).

### 17.4 Web UI
`ResumeButton.tsx` in the reader header `.session-meta` row (mono row styling). `SessionDetail` gains **`on_disk: bool`** (one stat at read time) → label **`⟲ resume`** vs **`⟲ restore & resume`** — honest affordance before the click. Hidden for archived sessions. Wiring = favorites/title/archive pattern: `postResume` (client.ts) → `useResumeSession()` (hooks.ts) invalidating the session query (`on_disk` flips after restore). Feedback: transient inline status next to the button, mode-derived; fallback modes render the command as selectable text. **No new toast system.**

### 17.5 Testing (no test spawns a process — CrontabIO precedent)
Pure: script generation (quoting, both branches), presence/restore matrix, mode selection. Runner-injected: exact `open` argv, captured failure. Routes (tmp source_root): 404/409, restore writes byte-identical file, response shapes, `on_disk` flip. Web: button states on-disk / archive-only (archived-hidden is structural — archived sessions 404 the reader detail route, so the page never renders; amended at plan-writing), mutation wiring, status rendering.

### 17.6 Docs
`docs/user/reading-room.md` gains "Resuming a conversation"; `INTROSPECT_TERMINAL_APP` documented with the other env vars; README mini-manual line.

### 17.7 Out of scope (named, not drifted)
Sidebar-row resume links; `introspect resume <uuid>` CLI verb (natural TUI-step-2 candidate); non-macOS launching; iTerm2 profile selection.
