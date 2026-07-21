# Developer guide

Minimal orientation for working on conversation-introspection. For user-facing docs, see
[`docs/user/`](../user/README.md).

## Architecture

The authoritative design document is the spec:

[`docs/superpowers/specs/2026-07-13-conversation-introspection-design.md`](../superpowers/specs/2026-07-13-conversation-introspection-design.md)

Read it first — the section numbers it defines (referenced as "§N" throughout the code and its
comments) are the shared vocabulary. The one-paragraph shape:

- **`server/`** — a Python 3.12 package (`introspect`) built on uv, SQLAlchemy, Alembic, and
  Pydantic v2. It owns ingest (capture → interpret), the archive schema and migrations, the
  tolerant record-parsing registry, FTS5 search, a FastAPI read layer, the CLI, and the Textual TUI.
- **`web/`** — a React + TypeScript reading room (Vite build, Vitest tests), served as static
  assets by the same `introspect serve` process that exposes the API.

The data model's four layers (identity → archive → interpretation → user data) and the
capture-then-interpret principle are explained for users in
[How the archive protects you](../user/how-the-archive-protects-you.md); the spec has the full rationale.

## Running the tests

The codebase is strictly test-driven: every existing feature was written test-first, and
contributions are expected to arrive with tests, not have them added in review.

**Server (Python):**

```bash
cd server
uv sync
uv run pytest                 # whole suite
uv run pytest tests/test_export_roundtrip.py -q   # one file
uv run ruff check .           # lint
```

Server tests are fixture-driven — real transcripts contain private content and never enter the
repo; synthetic fixtures (`server/tests/fixtures/`, wired through `conftest.py`) stand in for every
record type and CLI era. Their slugs, session uuids, and agent identifiers are a **pinned contract**
that later tests hardcode, so treat those constants as load-bearing. The API's tests live in
`tests/test_api_*.py`, one file per router. `tests/test_installer.py` drives the repo-root
`install.sh` end-to-end in a hermetic sandbox (scratch `HOME`, scripted fake `uv`/`npm`/`node`/…),
so the installer has real coverage without ever touching your machine.

**Web (TypeScript):**

```bash
cd web
npm ci
npm test          # vitest
npm run lint
npm run build     # tsc -b && vite build (also the artifact `introspect serve` serves)
```

## The flagship test: import → export round trip

`server/tests/test_export_roundtrip.py` imports a fixture tree, exports it back out, and
byte-compares the result to the source. It is the project's definition of correctness — the
byte-faithful [export](../user/export.md) guarantee, enforced. If a change can't survive the round
trip, it isn't done.

## The schema-extension loop (adding a new `introspect-schema/N`)

Claude Code's transcript format drifts: new CLI versions add or rename fields. The schema is
tolerant (`parse_line` never raises; forward drift is an `info` anomaly, an unknown record type a
`warn`, broken JSON an `error`), so drift never loses data — it surfaces as anomalies you then teach
the schema about. The running generation is `SCHEMA_VERSION` in
`server/src/introspect/schema/v1.py` (currently `introspect-schema/4`). The precedent for extending
it — versions /2, /3, and /4 — is right there in the same file's `DIFF_NOTES`. The loop:

1. **Observe the drift.** After a CLI upgrade, `introspect status` shows a jump in `info` (or
   `warn`/`error`) anomalies. The anomaly `kind`s and `detail` (field *names*, not content) tell you
   what's new.
2. **Declare the fields** at their verified locations on the relevant Pydantic record models in
   `schema/v1.py`. Prefer opaque/`Any` for payloads you don't need to interpret yet — the point is to
   stop them registering as drift, not to model everything. (Version /3 shows the other kind of
   change: interpretation-only, declaring no new fields.)
3. **Bump `SCHEMA_VERSION`** to the next `introspect-schema/N` and add its `DIFF_NOTES[N]` entry —
   one honest paragraph describing what changed and, ideally, the before/after anomaly floor.
4. **Add a migration** under `server/alembic/versions/` only if you changed a *storage* table. Pure
   schema-registry (interpretation) changes need no migration — that's the point of the archive /
   interpretation split. The `schema_versions` provenance table (migration `0005`) records which
   generations an archive has met; the running version's row is stamped the first time import or
   reparse runs.
5. **Rebuild from the archive:** `uv run introspect reparse` re-interprets the stored raw bytes under
   the new schema — no source files needed, since the bytes are already captured. Watch the anomaly
   floor drop.
6. **Add tests** for the newly-declared shapes (a fixture line exercising each) before the code, and
   confirm the round-trip test still passes.

This is exactly how the ~17,500-anomaly production drift event was resolved: the fields were
declared, `reparse` rebuilt the affected records from bytes already on disk, and the floor collapsed
to a handful.

## House rules

- Keep diffs small and self-contained; prefer new modules over growing large files.
- Match existing style; run `ruff` (server) and `eslint`/`prettier` (web) before proposing changes.
- Leave a `NOTE(...)` inline comment when a decision isn't obvious from the code — that's the
  established convention throughout this codebase.
