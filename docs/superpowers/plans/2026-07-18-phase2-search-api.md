# Phase 2: Search + API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Git rule (overrides skill templates):** Donovan owns pushes; the controller commits per approved plan (authored as Claude). Workers only `git add` — never commit.

**Goal:** Full-text search over the archive (FTS5 behind the `SearchIndex` interface) and a localhost FastAPI read layer (`/api/v1`) with favorites and an import trigger — the server the Phase 3 UI will sit on.

**Architecture:** Search index maintenance is explicit, not trigger-based: `interpret.apply` indexes text blocks as it creates them; interpretation-row deletion paths call `delete_for_records`. The API is read-mostly over the existing models; the only writers are favorites (own table, never touched by import/reparse) and the import trigger (which just invokes Phase 1's `run_import` under its existing lock). Everything binds 127.0.0.1.

**Tech Stack:** Adds `fastapi`, `uvicorn` to the existing Python 3.12/uv/SQLAlchemy/Alembic/Pydantic stack. Tests: FastAPI `TestClient` (no live server needed).

**Spec:** `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` §4 (favorites), §7 (search), §8 (API) — the authority when this plan is ambiguous. Phase 1 code contracts (capture/interpret/reparse/run docstrings + NOTE(claude) comments) are binding; read before modifying.

## Global Constraints

- FTS5 usage is confined to `search/fts5.py` and migration raw SQL — nothing SQLite-flavored leaks above the `SearchIndex` interface (Postgres path, spec §13).
- Only `block_kind='text'` blocks are indexed (spec §2: thinking deferred, tool content deferred). Index maintenance is EXPLICIT calls — no SQLite triggers.
- Only records of primary source files have interpretation rows (Phase 1 invariant) — the index therefore only ever holds primary-file content; deletion paths must keep that true.
- API: `/api/v1` prefix; errors are problem-details JSON `{status, title, detail}`; server binds `127.0.0.1` only; no auth.
- Pagination: `limit` (default 50, max 200) + `offset` everywhere a list returns; responses carry `total`.
- User query text is sanitized before FTS5 `MATCH` — bare quotes/operators must not 500; quoted phrases supported.
- `favorites` rows are never created/modified/deleted by import, reparse, or migrations backfill — only by the favorites endpoints.
- All new datetime columns use `db.UTCDateTime`. Type hints on public functions. ruff clean.
- Existing 131 tests stay green — Phase 1 behavior must not regress except where this plan explicitly extends it (FTS maintenance hooks).

## File Structure

```
server/src/introspect/
  search/__init__.py        # SearchIndex protocol + SearchHit dataclass + get_search_index()
  search/fts5.py            # FTS5 implementation (index/delete/search/sanitize)
  api/__init__.py           # create_app() factory
  api/deps.py               # engine/session dependencies (app.state engine)
  api/errors.py             # problem-details handlers (404/409/422/500)
  api/models.py             # Pydantic response models (SessionSummary, MessageOut, BlockOut, ...)
  api/routes/sessions.py    # /projects /sessions /sessions/{uuid} /transcripts/{id}/messages
  api/routes/search.py      # /search
  api/routes/favorites.py   # PUT/DELETE /sessions/{uuid}/favorite
  api/routes/admin.py       # /import /import/runs /status /anomalies /sessions/{uuid}/export.jsonl
  cli.py                    # + `serve` subcommand
  ingest/interpret.py       # + index maintenance hooks (surgical)
  ingest/reparse.py         # + index rebuild integration (surgical)
alembic/versions/0002_search_favorites.py
tests/ test_search_fts5.py test_search_integration.py test_api_sessions.py
       test_api_search.py test_api_favorites.py test_api_admin.py
```

Boundaries: `search/` knows models but not FastAPI; `api/` knows models + search but never touches `ingest` internals except `run_import`/lock helpers; `ingest` knows `search` only via the `SearchIndex` interface.

---

### Task 1: Migration 0002 — favorites, FTS5 table, primary-uniqueness index

**Files:**
- Create: `server/alembic/versions/0002_search_favorites.py`
- Modify: `server/src/introspect/models.py` (add `Favorite`)
- Test: `server/tests/test_migration_0002.py`

**Interfaces:**
- Produces: `Favorite` ORM model — `session_uuid (PK, FK sessions.session_uuid), created_at (UTCDateTime)`. Migration 0002 additionally (raw SQL), IN THIS ORDER:
  1. **FTS5 availability preflight:** attempt `CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)` + drop; on failure raise with a clear message ("SQLite build lacks FTS5") — cryptic mid-migration failure is forbidden (Opus m5).
  2. **Primary-invariant preflight (Opus M4):** `SELECT transcript_id FROM source_files WHERE is_primary GROUP BY transcript_id HAVING COUNT(*)>1` — if any rows, raise listing the transcript_ids (clear remediation error) BEFORE creating the constraint. A populated archive with a latent double-primary must fail loudly and actionably, not brick import/serve cryptically.
  3. Create `content_fts` = `fts5(text_content, content='content_blocks', content_rowid='id', tokenize='porter unicode61')`.
  4. **Backfill atomically with creation (Opus B1):** `INSERT INTO content_fts(rowid, text_content) SELECT id, text_content FROM content_blocks WHERE block_kind='text' AND text_content IS NOT NULL AND text_content<>''` — the EXACT incremental-indexing predicate. (NOT FTS5's native `'rebuild'`: external-content rebuild would index every row including tool/thinking/NULL, diverging from the text-only index.) Auto-migration runs on every subcommand and on the next cron tick — an empty index over a populated archive would make /search look like data loss.
  5. Create partial unique index `uq_one_primary_per_transcript ON source_files(transcript_id) WHERE is_primary`.
- Downgrade drops the index, `content_fts`, and `favorites`.
- Additional test: `test_migration_backfills_existing_blocks` — build archive on 0001... impractical; instead: capture fixture tree on a fresh DB (which migrates through 0002 with empty content_blocks), then simulate pre-existing-content backfill by `DELETE FROM content_fts` + re-running the backfill INSERT statement → search finds fixture phrases again. Plus `test_migration_preflight_rejects_double_primary`: on a fresh DB, insert a synthetic double-primary pair with the index dropped... simpler: unit-test the preflight query helper directly against a session with two is_primary rows for one transcript (constraint dropped via raw SQL first).

- [ ] **Step 1: Write failing tests**

```python
def test_migration_0002_creates_objects(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    names = set(inspect(engine).get_table_names())
    assert "favorites" in names
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='content_fts'").fetchone()
        assert row is not None
        idx = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='uq_one_primary_per_transcript'"
        ).fetchone()
        assert idx and "WHERE is_primary" in idx[0]


def test_partial_unique_index_enforces_single_primary(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    sf = db_session.query(SourceFile).filter_by(is_primary=True).first()
    dup = SourceFile(project_id=sf.project_id, transcript_id=sf.transcript_id,
                     path=sf.path + ".copy", kind="main", is_primary=True, generation=0,
                     byte_offset_checkpoint=0, last_size=0, prefix_hash="", status="active",
                     first_seen_at=utcnow(), last_seen_at=utcnow())
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_migration_0002.py -q` — FAIL
- [ ] **Step 3: Implement** (model + migration; verify Phase 1 divergence tests still pass — `_handle_divergence` demotes before inserting the new primary, so the constraint must hold through that sequence; if flush-ordering breaks it, demote+flush before insert).
- [ ] **Step 4: Full suite** — all green (131 + new). **Step 5: Stage** — `git add server/`

---

### Task 2: SearchIndex interface + FTS5 implementation

**Files:**
- Create: `server/src/introspect/search/__init__.py`, `server/src/introspect/search/fts5.py`
- Test: `server/tests/test_search_fts5.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class SearchHit:
      session_uuid: str
      transcript_id: int
      message_id: int
      record_uuid: str | None
      block_id: int
      block_index: int
      block_kind: str       # 'text' for all v1 hits; field exists for the thinking-ready design (spec §7)
      snippet: str          # with <mark>...</mark> around matches
      rank: float
      timestamp: datetime | None

  class SearchIndex(Protocol):
      def index_blocks(self, db: Session, block_ids: list[int]) -> int: ...
      def delete_for_blocks(self, db: Session, block_ids: list[int]) -> int: ...
      def delete_all(self, db: Session) -> None: ...
      def search(self, db: Session, query: str, *, session_uuid: str | None = None,
                 limit: int = 50, offset: int = 0) -> tuple[list[SearchHit], int]: ...
      def rebuild(self, db: Session) -> int: ...   # delete_all + index every text block

  def get_search_index() -> SearchIndex:   # returns the FTS5 impl (config point for tsvector later)
  def sanitize_query(raw: str) -> str:     # exposed for tests
  ```
- FTS5 semantics: external-content table — `index_blocks` inserts `(rowid, text_content)` for blocks with `block_kind='text'` AND non-empty text (others silently skipped; return count indexed).
- **`delete_for_blocks` — THE FTS5 external-content trap (Opus B2), contract is binding:** FTS5 stores no copy of the text; the delete form `INSERT INTO content_fts(content_fts, rowid, text_content) VALUES('delete', :id, :text)` requires the ORIGINAL indexed text, and issuing it for a never-indexed row (or with wrong text) corrupts the index (orphaned postings; later snippet() can raise "database disk image is malformed"). Therefore: **precondition (documented in the docstring): callers MUST call delete_for_blocks BEFORE deleting the content_blocks rows.** Implementation re-reads text from the still-present rows with the exact index predicate: `SELECT id, text_content FROM content_blocks WHERE id IN (:ids) AND block_kind='text' AND text_content IS NOT NULL AND text_content<>''` and issues per-row 'delete' only for those. Ids outside the predicate are skipped (they were never indexed). Add test: delete_for_blocks on a mixed id list (text + tool_use + already-deleted id) deindexes only the text block and corrupts nothing (subsequent search + snippet() still work). `search` joins `content_fts` rowids back through content_blocks→messages→transcripts for hit metadata, ordered by bm25, `snippet(content_fts, 0, '<mark>', '</mark>', '…', 12)`; second tuple element is total match count (separate `COUNT(*)` query, same filter). `session_uuid` filter applies via the join.
- `sanitize_query`: strip FTS5 operators from bare terms (`" ( ) * : ^ -` handling), preserve user-quoted phrases (balanced `"` pairs pass through as phrase tokens), AND-join remaining terms; empty result after sanitize → return `('', 0 hits)` without querying. Property: NO input may raise from `search` — malformed input degrades to no results.

- [ ] **Step 1: Write failing tests** (representative set — write all):

```python
def test_index_and_search_roundtrip(db_session, indexed_fixture):
    hits, total = idx.search(db_session, "horizon")
    assert total >= 1 and "<mark>" in hits[0].snippet


def test_search_scoped_to_session(db_session, indexed_fixture):
    hits, _ = idx.search(db_session, "horizon", session_uuid=OTHER_SESSION)
    assert hits == []


def test_only_text_blocks_indexed(db_session, indexed_fixture):
    n = idx.index_blocks(db_session, [tool_use_block_id])
    assert n == 0


def test_delete_for_blocks_removes_hits(db_session, indexed_fixture): ...
def test_rebuild_from_scratch_matches_incremental(db_session, indexed_fixture): ...


@pytest.mark.parametrize("evil", ['"unbalanced', 'a AND OR', 'x NEAR/3 y', '(paren', '*star', 'col:on', '-minus', '', '   '])
def test_sanitize_never_raises(db_session, indexed_fixture, evil):
    hits, total = idx.search(db_session, evil)   # must not raise


def test_quoted_phrase_matches_exactly(db_session, indexed_fixture):
    hits, _ = idx.search(db_session, '"still water"')
    assert all("still water" in h.snippet.lower().replace("<mark>", "").replace("</mark>", "") for h in hits)
```

(`indexed_fixture` = conftest fixture: capture fixture_tree, then `get_search_index().rebuild(db)`. Fixture builders gain distinctive searchable phrases — extend `make_user_line`/`make_assistant_line` call sites in the tree builder with known text: "the horizon band maps hours", "still water runs deep", etc.)

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement.** **Step 4: Full suite green + ruff.** **Step 5: Stage.**

---

### Task 3: Index maintenance wired into ingest paths

**Files:**
- Modify: `server/src/introspect/ingest/interpret.py`, `server/src/introspect/ingest/reparse.py`
- Test: `server/tests/test_search_integration.py`

**Interfaces:**
- `interpret.apply`: after creating a message's ContentBlock rows (primary-file records only, unchanged), `db.flush()` so the new rows have ids (apply currently never flushes — Opus m1), then call `get_search_index().index_blocks(db, new_text_block_ids)` in the SAME transaction as the rows (index rows commit/rollback atomically with their content — the `_interpret_chunk`/sweep containment semantics then apply to indexing for free).
- `interpret.remove_interpretation_for_source_file`: before deleting content_blocks, collect their ids and call `delete_for_blocks` (FK-safe order preserved).
- `reparse.reparse_all`: call `delete_all` where interpretation rows are wiped; blocks re-index via `apply` naturally. Reparse remains status-idempotent (whitespace grading unaffected).
- **Backfill contract:** after this task, `introspect reparse` fully populates the index for a pre-Phase-2 archive. No separate backfill command (YAGNI).

- [ ] **Step 1: Write failing tests**

```python
def test_capture_indexes_new_text_blocks(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    hits, total = get_search_index().search(db_session, "horizon")
    assert total >= 1


def test_divergence_cleanup_deindexes_old_generation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    # divergence recipe: copy the exact rewrite sequence from
    # tests/test_capture_integrity.py::test_divergence_detected_and_regenerated
    # (write REWRITTEN first line + original tail, then _capture_all again)
    hits, _ = get_search_index().search(db_session, "REWRITTEN")
    assert hits            # new generation searchable
    # old generation's replaced first-line text no longer findable twice:
    all_hits, total = get_search_index().search(db_session, SHARED_ASSISTANT_PHRASE)
    assert len({h.block_id for h in all_hits}) == len(all_hits)   # no duplicate blocks


def test_reparse_rebuilds_index_identically(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    _, before = get_search_index().search(db_session, "horizon")
    reparse_all(db_session)
    _, after = get_search_index().search(db_session, "horizon")
    assert before == after


def test_interpret_failure_rolls_back_index_with_rows(db_session, fixture_tree, monkeypatch):
    # monkeypatch interpret.apply to create blocks then raise for ONE record type
    # (pattern: tests/test_capture.py::test_interpret_failure_never_rolls_back_capture);
    # after capture: search for that record's distinctive text -> zero hits (the
    # rolled-back blocks and their index rows died together)
```

- [ ] **Step 2: FAIL.** **Step 3: Implement (surgical — respect every existing NOTE(claude)).** **Step 4: Full suite green (131 Phase 1 tests included) + ruff.** **Step 5: Stage.**

---

### Task 4: FastAPI skeleton — app factory, deps, errors, serve command

**Files:**
- Create: `server/src/introspect/api/__init__.py`, `api/deps.py`, `api/errors.py`, `api/models.py`
- Modify: `server/pyproject.toml` (runtime: `fastapi>=0.110`, `uvicorn>=0.29`; dev group: `httpx>=0.27` — TestClient hard-requires it, without it every API test dies at collection (Opus M2)), `server/src/introspect/cli.py` (add `serve`)
- Test: `server/tests/test_api_skeleton.py`

**Interfaces:**
- Produces: `create_app(db_path: Path | None = None, source_root: Path | None = None) -> FastAPI` — opens engine, `upgrade_to_head`, stores sessionmaker + resolved source_root on `app.state` (source_root threads through to the import endpoint — Opus M3a); dependency `get_db` yields a session per request. Problem-details handlers registered for: `LookupError` subclasses → 404; `RequestValidationError` AND `StarletteHTTPException` → restyled into problem JSON (FastAPI validation does NOT raise ValueError — Opus m2); unhandled → 500 `{status, title, detail}` (detail = exception class name only, no internals). `GET /api/v1/health` → `{"status": "ok"}`.
- CLI: `introspect serve [--db PATH] [--port 8765] [--host 127.0.0.1]` → uvicorn. Default host hardcoded-default `127.0.0.1`; `--host` exists but README/help text warns about non-local binds.
- API response models in `api/models.py` (used from Task 5 on): `SessionSummary(session_uuid, project_slug, ai_title, custom_title, started_at, last_activity_at, message_count, favorite: bool)`, `SessionDetail(SessionSummary + transcripts: list[TranscriptInfo])`, `TranscriptInfo(id, kind, agent_hex_id, agent_type, agent_description)`, `MessageOut(record_uuid, parent_uuid, type, model, timestamp, blocks: list[BlockOut])`, `BlockOut(block_index, block_kind, text_content, tool_name, is_error)`, `Problem(status, title, detail)`.

- [ ] Steps: failing tests (health 200; unknown path → problem-JSON 404 shape; TestClient boots on tmp DB) → implement → full suite + ruff → stage.

---

### Task 5: Sessions + projects + messages endpoints

**Files:**
- Create: `server/src/introspect/api/routes/sessions.py`
- Modify: `api/__init__.py` (include router)
- Test: `server/tests/test_api_sessions.py`

**Interfaces (spec §8 verbatim where stated):**
- `GET /api/v1/projects` → `[{id, dir_slug, resolved_cwd, session_count}]`
- `GET /api/v1/sessions?title=&favorite=&project=&limit=&offset=` → `{items: [SessionSummary], total}` — `last_activity_at DESC NULLS LAST`; `title` = case-insensitive substring over ai_title OR custom_title (plain LIKE, spec §7); `favorite=1` filters to favorited; `project` = dir_slug.
- `GET /api/v1/sessions/{uuid}` → SessionDetail (404 problem if unknown). `message_count` = messages in main transcript.
- `GET /api/v1/transcripts/{id}/messages?offset=&limit=&around=<record_uuid>` → `{items: [MessageOut], total, offset}` — ordered by `Message.id` alone (timestamp is nullable — NULL rows would corrupt ordinal math; id order == insertion order == file order for a transcript — Opus m4); `around` centers the returned page on that record_uuid (ordinal = COUNT(*) WHERE id < target.id, offset = max(0, ordinal - limit//2)); 404 problem for unknown transcript or unknown around-uuid; serves main and subagent transcripts identically (lazy drill-in).

- [ ] Steps: failing tests (list ordering desc; title filter matches ai and custom; unknown session 404 problem shape; messages paging totals; `around` centering lands the target uuid in the returned page; subagent transcript messages served) → implement → full suite + ruff → stage.

---

### Task 6: Search endpoint

**Files:**
- Create: `server/src/introspect/api/routes/search.py`
- Test: `server/tests/test_api_search.py`

**Interfaces:**
- `GET /api/v1/search?q=&scope=global|session&session=&limit=&offset=`
  - `scope=global` (default): `{groups: [{session: SessionSummary, hits: [HitOut]}], total}` — hits grouped by session, groups ordered by best (lowest) bm25 rank within; group cap: hits per session capped at 5 with `has_more: bool` per group.
  - `scope=session` (requires `session=`; 422 problem if missing): `{items: [HitOut], total}` flat, rank order.
  - `HitOut(record_uuid, transcript_id, block_index, snippet, timestamp)`.
  - Empty/whitespace `q` → 422 problem. Sanitizer guarantees no 500 on any q (Task 2 property).

- [ ] Steps: failing tests (global grouping + per-group cap + has_more; session scope flat; missing session param 422; evil-input parametrize returns 200 with zero hits) → implement → full suite + ruff → stage.

---

### Task 7: Favorites endpoints

**Files:**
- Create: `server/src/introspect/api/routes/favorites.py`
- Test: `server/tests/test_api_favorites.py`

**Interfaces:**
- `PUT /api/v1/sessions/{uuid}/favorite` → 204 (idempotent — second PUT still 204); 404 problem for unknown session.
- `DELETE /api/v1/sessions/{uuid}/favorite` → 204 (idempotent — deleting a non-favorite is 204).
- `GET /sessions` reflects `favorite` immediately; `favorite=1` filter round-trips.
- Guard test: run `run_import` + `reparse_all` after favoriting → favorite rows untouched (the spec §4 "never touched" invariant, proven).

- [ ] Steps: failing tests → implement → full suite + ruff → stage.

---

### Task 8: Admin endpoints — import trigger, runs, status, anomalies, export

**Files:**
- Create: `server/src/introspect/api/routes/admin.py`
- Test: `server/tests/test_api_admin.py`

**Interfaces:**
- `POST /api/v1/import` (spec §8: "202 + run id" — binding, Opus M1): under a non-blocking lock probe (reuse `run.py`'s `_acquire_lock`/`_release_lock`), CREATE the ImportRun row (trigger='api', status='running') in the request handler and return 202 `{run_id: <id>}`; lock held → 409 problem `{title: "import already running"}`, no row. A worker thread then executes the import against its own engine. **Requires a surgical Phase 1 change, called out explicitly:** `run_import` gains optional `run_id: int | None = None` — when provided, it finalizes THAT row instead of creating one (all Phase 1 callers unchanged; existing tests must stay green). The probe-then-thread race stays benign for integrity (run_import's own lock is the real gate). Race contract: if the worker thread's run_import reports 'already_running' (lock lost to a concurrent cron run after our probe), the thread finalizes the pre-created row — status='errors', finished_at set, counts zeroed — so no row is ever stranded in 'running' and the client polling its run_id sees an honest terminal state. Document in run.py's docstring; reviewer scrutiny requested on this seam.
- `GET /api/v1/import/runs?limit=&offset=` → ImportRun rows desc; `GET /api/v1/import/runs/{id}` → row or 404 problem.
- `GET /api/v1/status` → `{sessions, files, records, archive_bytes, anomalies: {error, warn, info}, last_run: {...}|null}` (mirrors CLI status; `archive_bytes` = DB file size).
- `GET /api/v1/anomalies?severity=&limit=&offset=` → `{items: [{id, severity, kind, detail, source_file_path, created_at}], total}` desc.
- `GET /api/v1/sessions/{uuid}/export.jsonl` → `StreamingResponse` over a GENERATOR yielding per-line bytes (add/reuse a public `export.iter_transcript_lines(db, session_uuid)` beside `export_transcript` — do not buffer the whole file via export_transcript, Opus m6), `application/x-ndjson`, `Content-Disposition: attachment; filename="<uuid>.jsonl"`, bytes identical to CLI export; 404 problem unknown session.

- [ ] Steps: failing tests (export bytes == export_transcript bytes; import 202 returns integer run_id, then POLL `GET /import/runs/{id}` with timeout (loop up to ~5s sleeping 0.1s) until status leaves 'running' → assert terminal status 'ok' and counts match the fixture tree — the worker thread must be joinable: expose the started Thread on `app.state.last_import_thread` and join it in the test before teardown (tmp DB deletion vs live thread — Opus M3); lock-held → 409 (hold lock in test); status shape; anomalies filter) → implement → full suite + ruff → stage.

---

### Task 9: Real-data verification (controller-run, like Phase 1's Task 13)

- [ ] `cd server && uv run introspect reparse` — backfills the FTS index over the production archive (records_reparsed ≈ 19.9K; anomaly floor unchanged at 21).
- [ ] `uv run introspect serve` (background) → curl checks against production data:
  - `/api/v1/status` counts match CLI status
  - `/api/v1/sessions?limit=5` — this session present, date-desc
  - `/api/v1/search?q=horizon+band` — hits from the design conversation
  - `/api/v1/search?q="byte-faithful"` — phrase search works
  - export.jsonl curl → `cmp` against CLI export → identical
  - PUT favorite on a real session → visible in list → DELETE
- [ ] Record results in `claude_notes/`; kill the server.

---

## Execution notes for the orchestrator

- Order: 1 → 2 → 3 are strictly sequential (each consumes the prior's objects). 4 independent of 2-3 (needs only Task 1's model import) — MAY run while 3 is in review, but 4's stage step touches pyproject/uv.lock: coordinate index adds. 5 → 6 → 7 → 8 sequential after 4 (+ 6 needs 2-3; 8 needs nothing from 5-7 but shares router wiring — keep sequential, files overlap in api/__init__.py). 9 last, controller-run.
- Reviewer calibration: full two-stage review for Tasks 2, 3, 8 (interface-defining / Phase-1-touching / concurrency); single review for 1, 5, 6; controller spot-check for 4, 7.
- Model calibration: opus for 2, 3, 5, 8; sonnet for 1, 4, 6, 7.
- Every implementer re-runs the FULL suite — Phase 1 regression protection is non-negotiable, especially Tasks 1 (migration touches divergence invariant) and 3 (touches interpret/reparse).
- Commit checkpoints (controller, authored as Claude): after Task 3 (search core), after Task 8 (API complete), after Task 9 (verified). Donovan reviews before any push, as always.
