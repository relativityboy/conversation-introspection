# conversation-introspection — Design

**Date:** 2026-07-13 · **Status:** draft for Donovan's review (sections 1–5 discussed live; 6–13 are Claude's take, pending review)
**One-liner:** A local-first, fully independent archive + reading room for Claude Code session transcripts. The filesystem is an ephemeral feed; our database is the system of record.

## 1. Purpose & core reframe

Claude Code writes session transcripts as append-only `.jsonl` under `~/.claude/projects/`, and the TUI **actively deletes older sessions**. Current history is already incomplete. Therefore this is **not a cache — it is an archive**:

- Capture is lossless (byte-faithful raw lines) and add-only; source deletion never propagates.
- The app works with zero source files present. Fully independent.
- We can reconstruct a byte-faithful `.jsonl` for any captured session at any time.
- User-generated data (favorites) is never derivable from sources and never touched by import/reparse.

## 2. Decisions already made (with Donovan, 2026-07-13)

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
5. **Restored-source dedup.** Donovan keeps out-of-band backups of transcripts (late-April onward); a restored backup arrives as the same session at a *different path*. Record-level identity makes that safe: within a transcript, an incoming line whose record_uuid + raw-byte hash already exist **in a different source file** is skipped (counted, not re-stored) — same-file matches are never skipped, since dropping a line from a file breaks its byte-faithful reconstruction (final-review finding, 2026-07-14); same record_uuid with *different* bytes → `error` anomaly, never a silent overwrite. Thin records without uuids dedup on (transcript, raw-byte hash, line_number). Consequence: "import from backup" is just import — no special mode.
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
- `GET /sessions?title=&favorite=&project=&limit=&offset=` — date desc; list-item shape includes titles, times, counts, favorite flag
- `GET /sessions/{uuid}` — detail incl. transcript inventory (subagent metadata only — no messages)
- `GET /transcripts/{id}/messages?offset=&limit=&around=<message_uuid>` — paged for virtualization; `around` centers a page on a deep-linked message; serves main and subagent transcripts uniformly (lazy drill-in)
- `GET /search?q=&scope=global|session&session=&limit=&offset=`
- `PUT /sessions/{uuid}/favorite` · `DELETE /sessions/{uuid}/favorite`
- `POST /import` → 202 + run id (409 if lock held) · `GET /import/runs?limit=` · `GET /import/runs/{id}`
- `GET /sessions/{uuid}/export.jsonl` — reconstruction download (same bytes as CLI export)
- `GET /status` — record/session counts, archive size, last import, anomaly summary
- `GET /anomalies?severity=&limit=&offset=`
- Errors: problem-details JSON (`{status, title, detail}`). No auth (localhost tool).

## 9. Web UI

**Layout (per Donovan's spec):** left sidebar — as-you-type title filter, favorites toggle, conversation list date-desc; main area — tab 1: all-content search (Enter-committed, URL-synced), tab 2: selected conversation with within-conversation search (Enter-committed, URL-synced). Added: bottom status bar (last import, counts, anomaly badge, import trigger button).

**URL scheme (React Router; all state shareable):**
- `/search?q=term` — global search tab
- `/s/{sessionUuid}` — conversation · `/s/{uuid}?q=term` — within-conversation search
- `/s/{uuid}/m/{messageUuid}` — deep link to message (fetch `around=`, scroll, glow-highlight)
- `/s/{uuid}/a/{agentHexId}[/m/{messageUuid}]` — subagent drill-in
- Sidebar state as params (`?fav=1&title=…`)

**Conversation rendering:** no chat bubbles — full-width blocks, 2px left voice-accent border, eyebrow (speaker · time), serif prose with rendered markdown + syntax-highlighted code. Tool use/result blocks collapsed by default (mono, size-aware; images decoded on demand). Thinking = marker glyph ("thinking occurred — content not persisted by the CLI"). Subagent dispatch renders a chip → drill-in route (lazy). Message lists virtualized (sessions reach thousands of records); deep-link arrival auto-expands any collapsed block containing the target (relevant today for messages, load-bearing later if tool-content indexing lands).

**Theme — "Still Water"** (Claude's stamp, mockup at `docs/design/2026-07-13-still-water-mockup.html`): blue-hour palette (`--depth #0B1220`, `--surface #111B2E`, text `#E7ECF4`), voice accents dawn amber `#E5A54B` (Donovan) / dragonfly cyan `#6FD3C7` (Claude), ember `#D4756B` for anomalies only. Serif conversation prose (Charter stack), mono for terminal material, sans chrome. Signature: the **horizon band** — an 8px gradient strip atop each conversation mapping its real time-span onto a day-cycle gradient (sessions crossing midnight/sunrise show it), echoed as a 2px micro-band per sidebar item. Motion limited to deep-link glow; reduced-motion respected. Dark-only v1.

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
- **`history.jsonl` ghost recovery (v2).** Measured 2026-07-13: 195 sessions in prompt history since 2026-01-19; only 12 transcripts survive (TUI ~30-day retention); 183 "ghost" sessions with 4,059 prompts have history.jsonl as their only trace. Deferred to v2 by decision (Donovan lean + Claude concurrence): the file is backed up and unpruned, so deferral loses nothing, while v1 scope stays on the time-sensitive work — capturing living transcripts. v2 shape when it comes: source kind `prompt_history`, its own schema model, `ghost` status on `sessions`, tombstone rendering (your-voice-only timeline, first-prompt title fallback). Note: Donovan's out-of-band transcript backups (late-April onward) mean many May "ghosts" are actually recoverable in full via restored-source import (§6.5) — permanent losses concentrate Jan–Apr.
- **Thinking-block search + UI toggle.** Designed (§2), ships if the CLI ever persists thinking text.

## Review log

- 2026-07-13: Donovan approved cron cadence (15 min), `.bak` capture, DB home `~/.conversation-introspection/`; `history.jsonl` deferred to v2 (rationale above). Sections 6–13 reviewed live in session.

## 14. Phase 4 — Publication readiness (added 2026-07-19, Donovan's feature set + Claude's resolutions)

Four features that make the reading room publishable. Donovan's UI specifications are binding where given verbatim below; Claude's design resolutions were reviewed and approved in-session. **Execution: a fresh instance** (this addendum + plan written by the 5-day session; build belongs to full headroom). Orientation for that instance includes walking the built room.

### 14.1 Sidebar content search
The sidebar's single input matches **title OR chat content** (was: title only). As-you-type (debounced 250ms) stays.
- Server: `GET /sessions` gains `q=` (replaces the title-only `title=` param; accept `title=` as a deprecated alias for one release; writes strip the legacy key). Matching: **session_uuid substring** (the uuid-esque string — Donovan addition 2026-07-19) OR title LIKE **as three ORs over user/ai/custom titles (find it wherever it lives — a user rename must not shadow an archive-title match; critique #5 resolution)** OR FTS content match. **Ordering: the union keeps the list contract `last_activity_at DESC NULLS LAST` — the snippet is a hint, never a re-rank (critique #2 resolution).** Response items gain `match_snippet: str | null` — populated (best-rank snippet, `<mark>`s included) only when the session matched by content and not by title.
- **SearchIndex Protocol extension (critique #1 — binding; protects the §13 Postgres promise):** ALL new FTS SQL stays behind the interface. Protocol gains `session_uuids_matching(db, q, project_slugs: list[str] | None) -> list[str]` and `best_snippet(db, session_uuid, q) -> str | None`; `search()` gains `project_slugs: list[str] | None = None`. Routes compose these — zero raw FTS SQL in `sessions.py`/`search.py`. Snippet strategy: one FTS pass per request for the page's content-matched sessions, never a query-per-session-per-keystroke.
- UI: content-matched sessions show a one-line mist snippet hint under the title (mark-splitting renderer reused). Sidebar URL param renamed `?filter=` (client accepts legacy `?title=` on read).

### 14.2 Project filter (the subsystem)
Scope everything by a chosen subset of projects. **Donovan's UI spec, binding verbatim:**
- Thin top bar (app level, above sidebar + main — spans both, since its context scopes both) showing a chip "all projects" with an 'x'.
- 'x' removes it and replaces with a SearchBox. SearchBox focused: down-arrow when empty → alphabetized select-list of projects; typing → select-list filtered on dir_slug `%str%`; **double-tap `<esc>`**: if select-list open OR text present → clear text + close list; else (list closed, box empty) → remove ALL selected project chips & filtering.
- Selecting a project in the list → adds a project chip to the right of the search box, clears the box. Chip 'x' removes that chip. Zero chips selected → bar reverts to the "all projects" chip.
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
One sticky toggle ("conversation only") hiding non-chat material. **Scope (approved): full prose mode** — hides system-type message rows AND tool_use/tool_result blocks inside kept messages; keeps prose and thinking glyphs. **Attachments: IN — `type IN ('user','assistant','attachment')` (Donovan ruling 2026-07-19: pasted things are things a human said).**
- Message-row filtering is **server-side** (windowing correctness: totals/offsets/around must be computed within the filtered set): `GET /transcripts/{id}/messages` gains `chat_only=1` → `type IN ('user','assistant')`.
- Block-level hiding (tool blocks within assistant messages) is client-side presentation (no pagination impact).
- Sticky via localStorage key `introspect.chatOnly.v1`; applies in main and subagent readers. Toggle lives in the conversation header, mist styling. Header keeps the unfiltered `message_count` and appends mist "· conversation only" while active (no second server count — critique #6 resolution).
- **Client plumbing (critique #3 — the implementer trap, binding):** `chat_only` threads through ALL THREE fetch sites in ConversationView (the `useMessages` seed AND both direct `fetchMessages` edge loaders), into the react-query key, and into the `MessageStream` remount key (`${transcriptId}:${around}:${chatOnly}`) so toggling re-seeds the window cleanly. Unfiltered edge pages against a filtered window corrupt the offset math — the plan must name all three sites.
- Edge (spec'd): a deep-link `around=` target that is a system message while `chat_only=1` → server 404s within the filtered set; the not-found notice then offers BOTH actions with distinct semantics (critique #12): "show all message types" (disables the toggle, re-seeds with the same `around=`) and the existing "view from the beginning" (keeps the toggle, offset 0).

### Execution notes
Same arc: plan (fresh instance writes it against this addendum) → Opus critique → SDD with per-task reviews → final review → **walk** (mandatory — Phase 3's walk caught what tests couldn't). Plan-authoring budget notes (critique #9): the `?title=`→`?filter=` rename touches urlState.ts helpers, all 10 urlState tests, and Sidebar test assertions — mechanical churn, budget it. Backlog explicitly NOT in Phase 4: per-message ¶ copy-link anchor (mockup-only gap), drift-floor schema loop (24→27), ghost recovery (§13), push-to-public decision (Donovan's).
