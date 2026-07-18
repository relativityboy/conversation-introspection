# Phase 1: Archive Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Git rule (overrides skill templates):** Donovan commits; workers only `git add`. Every "Commit" step in the canonical template is a **Stage** step here.

**Goal:** A working importer + archive: every Claude Code transcript line captured byte-faithfully into SQLite, interpreted through a versioned schema registry, reconstructable to identical bytes, driven by a cron-safe CLI.

**Architecture:** Capture-then-interpret pipeline (spec §6). Raw lines land in `raw_records` unconditionally; a versioned Pydantic registry interprets them into normalized tables beside — never instead of — capture. Anomalies are recorded, never fatal. Export re-emits the primary source file's bytes.

**Tech Stack:** Python 3.12, uv, SQLAlchemy 2.x, Alembic, Pydantic v2, pytest, ruff. Stdlib `argparse` for CLI (no dep needed for 4 subcommands).

**Spec:** `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` — the authority when this plan is ambiguous.

## Global Constraints

- DB default `~/.conversation-introspection/archive.db`; overridable via `INTROSPECT_DB` env var or `--db` flag. Source root default `~/.claude/projects`; overridable via `INTROSPECT_SOURCE_ROOT` or `--source-root`. Tests always use tmp paths for both.
- SQLite pragmas on every connection: WAL mode, `busy_timeout=5000`, `foreign_keys=ON`.
- `raw_line` stores the line's exact bytes **including its trailing newline if present** (final line may lack one). Reconstruction is pure concatenation. This is the byte-faithfulness contract; never strip/normalize.
- Stream files line-by-line (lines up to ~528KB exist); never read whole files into memory.
- Fixtures are **synthetic only** — real transcripts contain private content and never enter the repo.
- Schema registry version string: `introspect-schema/1`.
- Interpretation failures produce `parse_anomalies` rows; only inability to open the DB is fatal (exit 2).
- Type hints on all public functions. Match ruff defaults.
- No new runtime dependencies beyond: sqlalchemy, alembic, pydantic. Dev: pytest, ruff.
- `favorites` and FTS tables are Phase 2 migrations (spec §4) — deliberately NOT in migration 0001.

## File Structure

```
server/
  pyproject.toml                     # uv project, deps, [project.scripts] introspect
  alembic.ini
  alembic/env.py, versions/0001_archive_core.py
  src/introspect/
    __init__.py
    config.py                        # db_path()/source_root() resolution (env > flag > default)
    db.py                            # engine/session factories, pragmas
    models.py                        # all SQLAlchemy ORM models (spec §4)
    schema/
      __init__.py                    # registry: parse_line() dispatch, ParseResult
      v1.py                          # Pydantic models for every known record type
    ingest/
      discovery.py                   # walk source root -> DiscoveredFile stream
      reader.py                      # byte-offset tail reader, partial-line safe
      capture.py                     # raw capture: checkpoints, dedup, divergence
      interpret.py                   # ParseResult -> messages/content_blocks/... rows
      reparse.py                     # rebuild interpretation from stored raw
      run.py                         # orchestrator: lock, sweep, import_runs
    export.py                        # byte-faithful session reconstruction
    cli.py                           # argparse: import/reparse/export/status
  tests/
    conftest.py                      # tmp-db fixture, synthetic fixture-tree builder
    test_schema_v1.py  test_discovery.py  test_reader.py
    test_capture.py    test_interpret.py  test_reparse.py
    test_export_roundtrip.py         # THE FLAGSHIP
    test_run.py        test_cli.py
```

Boundaries: `schema/` knows nothing about the DB. `ingest/capture.py` writes archive tables only; `ingest/interpret.py` writes interpretation tables only; `run.py` is the only module that composes them. `export.py` reads `raw_records` only.

---

### Task 1: Project scaffold

**Files:**
- Create: `server/pyproject.toml`, `server/src/introspect/__init__.py`, `server/tests/test_sanity.py`, `server/.python-version`

**Interfaces:**
- Produces: importable `introspect` package; `uv run pytest` green; `introspect` console script entry (wired fully in Task 12).

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "introspect"
version = "0.1.0"
description = "Archive + reading room for Claude Code session transcripts"
requires-python = ">=3.12"
dependencies = ["sqlalchemy>=2.0", "alembic>=1.13", "pydantic>=2.7"]

[project.scripts]
introspect = "introspect.cli:main"

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/introspect"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create package + sanity test**

`src/introspect/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/test_sanity.py`:
```python
import introspect


def test_package_imports():
    assert introspect.__version__ == "0.1.0"
```

Also write `.python-version` containing `3.12`.

- [ ] **Step 3: Sync and run**

Run: `cd server && uv sync && uv run pytest -q`
Expected: `1 passed`

- [ ] **Step 4: Stage**

```bash
git add server/pyproject.toml server/src server/tests server/.python-version server/uv.lock
```

---

### Task 2: Config, DB factories, ORM models, migration 0001

**Files:**
- Create: `server/src/introspect/config.py`, `server/src/introspect/db.py`, `server/src/introspect/models.py`, `server/alembic.ini`, `server/alembic/env.py`, `server/alembic/versions/0001_archive_core.py`
- Test: `server/tests/test_models.py`

**Interfaces:**
- Produces: `config.db_path(cli_value: str | None = None) -> Path`, `config.source_root(cli_value: str | None = None) -> Path` (precedence: explicit arg > env > default). `db.get_engine(db_path: Path) -> Engine` (applies pragmas via `event.listens_for(engine, "connect")`), `db.session_factory(engine) -> sessionmaker`. ORM classes exactly: `Project`, `ChatSession` (table `sessions` — named to avoid colliding with `sqlalchemy.orm.Session`, which every `db:` parameter in this plan refers to), `Transcript`, `SourceFile`, `RawRecord`, `ImportRun`, `ParseAnomaly`, `Message`, `ContentBlock`, `TokenUsage`, `SessionEvent` with columns per spec §4. `db.upgrade_to_head(engine)` runs Alembic programmatically (CLI + tests share it). `db.UTCDateTime` — a `TypeDecorator(String)` storing ISO-8601 UTC and returning tz-aware datetimes — is used for EVERY datetime column (SQLite's DateTime round-trips naive and would crash aware/naive comparisons in fresh processes; Opus review M2).

**Column contracts later tasks rely on (verbatim):**
- `SourceFile`: `id, project_id, transcript_id, path (unique), kind, is_primary (bool), byte_offset_checkpoint (int, default 0), last_size (int), prefix_hash (str), status ('active'|'gone_at_source'|'diverged'), first_seen_at, last_seen_at, gone_detected_at`
- `RawRecord`: `id, source_file_id, transcript_id, line_number (int, per file), byte_offset (int), raw_line (LargeBinary), line_sha256 (str, hex), record_type (str|None), record_uuid (str|None), detected_cli_version (str|None), parsed_with_schema_version (str|None), parse_status ('ok'|'partial'|'anomaly'), ingested_at`  — unique constraint `(source_file_id, line_number)`; index `(transcript_id, record_uuid)`, index `(transcript_id, line_sha256)`
- `ChatSession` (table `sessions`): `session_uuid (PK, str)`, `project_id`, `started_at`, `last_activity_at`, `ai_title`, `custom_title`
- `Transcript`: `id, session_id (FK sessions.session_uuid), kind ('main'|'subagent'), agent_hex_id (str|None), agent_type (str|None), agent_description (str|None), parent_tool_use_id (str|None)` — unique `(session_id, kind, agent_hex_id)`
- `Message`: `id, raw_record_id (unique FK), transcript_id, record_uuid, parent_uuid, timestamp (datetime|None), type, model, cwd, git_branch, request_id`
- `ContentBlock`: `id, message_id, block_index, block_kind, text_content (Text|None), tool_name, tool_use_id, is_error (bool|None), payload (JSON|None)`
- `TokenUsage`: `id, message_id (unique FK), input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens` (ints, nullable)
- `SessionEvent`: `id, raw_record_id (unique FK), session_id, event_kind, payload (JSON)`
- `ImportRun`: `id, trigger ('cli'|'api'), started_at, finished_at, files_seen, records_added, records_skipped_duplicate, anomaly_count, status ('running'|'ok'|'errors'|'fatal')`
- `ParseAnomaly`: `id, raw_record_id (nullable FK), source_file_id (nullable FK), severity ('info'|'warn'|'error'), kind (str), detail (JSON), schema_version, created_at`
- `Project`: `id, dir_slug (unique), resolved_cwd (str|None), first_seen_at`

- [ ] **Step 1: Write failing test**

`tests/test_models.py`:
```python
from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


EXPECTED_TABLES = {
    "projects", "sessions", "transcripts", "source_files", "raw_records",
    "import_runs", "parse_anomalies", "messages", "content_blocks",
    "token_usage", "session_events",
}


def test_migration_creates_all_tables(tmp_path: Path):
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_wal_mode_enabled(tmp_path: Path):
    engine = get_engine(tmp_path / "t.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_models.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`config.py`:
```python
import os
from pathlib import Path

DEFAULT_DB = Path.home() / ".conversation-introspection" / "archive.db"
DEFAULT_SOURCE_ROOT = Path.home() / ".claude" / "projects"


def db_path(cli_value: str | None = None) -> Path:
    p = Path(cli_value or os.environ.get("INTROSPECT_DB") or DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def source_root(cli_value: str | None = None) -> Path:
    return Path(cli_value or os.environ.get("INTROSPECT_SOURCE_ROOT") or DEFAULT_SOURCE_ROOT)
```

`db.py`:
```python
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker


def get_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def upgrade_to_head(engine: Engine) -> None:
    cfg = AlembicConfig(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    cfg.attributes["connection"] = engine.connect()
    try:
        command.upgrade(cfg, "head")
    finally:
        cfg.attributes["connection"].close()
```

`models.py` — SQLAlchemy 2.0 declarative with `Mapped[...]`/`mapped_column(...)` for every table + column named in the contracts above (write them all out; `raw_line: Mapped[bytes] = mapped_column(LargeBinary)`; JSON columns via `sqlalchemy.JSON`; datetimes timezone-aware UTC). Unique constraints and indexes exactly as listed.

`alembic/env.py` — standard template, but honor `config.attributes.get("connection")` when present (programmatic upgrades share the engine); `target_metadata = introspect.models.Base.metadata`.

`alembic/versions/0001_archive_core.py` — `op.create_table(...)` for all 11 tables mirroring `models.py` (autogenerate offline, then check in the reviewed file).

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_models.py -q` — Expected: 2 passed

- [ ] **Step 5: Stage** — `git add server/src/introspect server/alembic server/alembic.ini server/tests/test_models.py`

---

### Task 3: Schema registry v1 (Pydantic) + synthetic fixtures

**Files:**
- Create: `server/src/introspect/schema/__init__.py`, `server/src/introspect/schema/v1.py`, `server/tests/fixtures/records.py`
- Test: `server/tests/test_schema_v1.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = "introspect-schema/1"`. `schema.parse_line(raw: bytes) -> ParseResult` where
  ```python
  @dataclass
  class Anomaly:
      severity: str          # 'info' | 'warn' | 'error'
      kind: str              # 'unknown_field' | 'unknown_record_type' | 'validation_error' | 'invalid_json'
      detail: dict

  @dataclass
  class ParseResult:
      record: BaseRecord | None   # validated model, None if unparseable/unknown
      record_type: str | None
      record_uuid: str | None
      detected_cli_version: str | None
      status: str                 # 'ok' | 'partial' (info/warn anomalies) | 'anomaly' (error)
      anomalies: list[Anomaly]
  ```
- Fixture module produces `make_user_line(...)`, `make_assistant_line(...)`, `make_thin_meta_line(kind, ...)`, `make_session_file(lines) -> bytes` used by every later test.

- [ ] **Step 1: Write failing tests** — representative set (write all of these):

```python
import json

from introspect.schema import SCHEMA_VERSION, parse_line
from tests.fixtures.records import make_assistant_line, make_user_line


def test_user_record_parses_ok():
    r = parse_line(make_user_line(text="hello world"))
    assert r.status == "ok" and r.record_type == "user" and r.record_uuid
    assert r.detected_cli_version == "2.1.202"


def test_assistant_blocks_extracted():
    r = parse_line(make_assistant_line(text="hi", with_thinking=True, with_tool_use=True))
    kinds = [b.kind for b in r.record.blocks()]
    assert kinds == ["thinking", "text", "tool_use"]


def test_unknown_extra_field_is_info_partial():
    line = make_user_line(extra={"futureField": 1})
    r = parse_line(line)
    assert r.status == "partial"
    assert [a.severity for a in r.anomalies] == ["info"]


def test_unknown_record_type_is_warn():
    r = parse_line(json.dumps({"type": "hologram", "sessionId": "s"}).encode())
    assert r.status == "partial" and r.record is None
    assert r.anomalies[0].kind == "unknown_record_type"


def test_invalid_json_is_error():
    r = parse_line(b"{not json")
    assert r.status == "anomaly" and r.anomalies[0].kind == "invalid_json"


def test_malformed_known_type_is_error():
    r = parse_line(json.dumps({"type": "assistant", "message": 42}).encode())
    assert r.status == "anomaly" and r.anomalies[0].kind == "validation_error"


def test_thin_meta_records_parse():
    from tests.fixtures.records import make_thin_meta_line
    for kind in ["ai-title", "custom-title", "mode", "permission-mode",
                 "last-prompt", "queue-operation", "agent-name"]:
        assert parse_line(make_thin_meta_line(kind)).status == "ok", kind
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_schema_v1.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`schema/v1.py` — Pydantic v2, all with `model_config = ConfigDict(extra="allow")`:
- `Envelope` base: `uuid: str | None`, `parentUuid: str | None`, `sessionId: str | None`, `timestamp: str | None`, `cwd: str | None`, `version: str | None`, `gitBranch: str | None`, `isSidechain: bool | None`, `type: str`.
- Block models: `TextBlock(type='text', text: str)`, `ThinkingBlock(type='thinking', thinking: str = "", signature: str | None)`, `ToolUseBlock(type='tool_use', id: str, name: str, input: dict)`, `ToolResultBlock(type='tool_result', tool_use_id: str | None, content: str | list | None, is_error: bool | None)`; discriminated by `type` with fallback `UnknownBlock` (extra=allow).
- `UserRecord(Envelope)`: `message: UserMessage` where `content: str | list[Block]`. `AssistantRecord(Envelope)`: `message: AssistantMessage` (`model`, `content: list[Block]`, `usage: Usage | None`). `SystemRecord(Envelope)`: `subtype/level/...` optional. `AttachmentRecord(Envelope)`.
- Thin metas (no envelope): `AiTitleRecord(type, aiTitle, sessionId)`, `CustomTitleRecord`, `ModeRecord(mode, sessionId)`, `PermissionModeRecord(permissionMode, sessionId)`, `LastPromptRecord(leafUuid, sessionId)`, `QueueOperationRecord(operation, content|None, sessionId, timestamp|None)`, `AgentNameRecord(agentName, sessionId)`, `FileHistorySnapshotRecord(type, messageId|None)` — snapshot payload deliberately NOT modeled (archive-only per spec).
- Every record implements `blocks() -> list[NormalizedBlock]` (empty for non-conversational); `NormalizedBlock = dataclass(kind, text, tool_name, tool_use_id, is_error, payload)` — the *only* shape `interpret.py` sees.

`schema/__init__.py`:
```python
REGISTRY: dict[str, type[BaseModel]] = {
    "user": UserRecord, "assistant": AssistantRecord, "system": SystemRecord,
    "attachment": AttachmentRecord, "ai-title": AiTitleRecord, "custom-title": CustomTitleRecord,
    "mode": ModeRecord, "permission-mode": PermissionModeRecord, "last-prompt": LastPromptRecord,
    "queue-operation": QueueOperationRecord, "agent-name": AgentNameRecord,
    "file-history-snapshot": FileHistorySnapshotRecord,
}


def parse_line(raw: bytes) -> ParseResult:
    # json.loads; on failure -> invalid_json error anomaly, status 'anomaly'
    # type lookup; unknown -> unknown_record_type warn, status 'partial', record None
    # model_validate; ValidationError -> validation_error error anomaly, status 'anomaly'
    # model_extra non-empty (recursively on the record model) -> one unknown_field info anomaly
    #   with the extra key names in detail, status 'partial'
    # else status 'ok'; extract uuid/version from validated model where present
```
(Write the body out fully — the comment lines above describe the exact branch order.)

`tests/fixtures/records.py` — includes `make_snapshot_line()` emitting a minimal `file-history-snapshot` record. Builders emit realistic-shape records (envelope fields with plausible uuids/timestamps, version default `2.1.202`) as compact JSON + `\n`, content entirely synthetic. `make_session_file(lines: list[bytes]) -> bytes` concatenates.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_schema_v1.py -q` — Expected: all pass

- [ ] **Step 5: Stage** — `git add server/src/introspect/schema server/tests`

---

### Task 4: Source discovery

**Files:**
- Create: `server/src/introspect/ingest/__init__.py`, `server/src/introspect/ingest/discovery.py`
- Modify: `server/tests/conftest.py` (add fixture-tree builder)
- Test: `server/tests/test_discovery.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class AgentMeta:
      agent_type: str | None
      description: str | None
      tool_use_id: str | None

  @dataclass
  class DiscoveredFile:
      path: Path
      project_slug: str        # source dir name, e.g. "-Users-donovan-projects--ai-jetwalls"
      session_uuid: str
      kind: str                # 'main' | 'subagent' | 'backup'
      agent_hex_id: str | None # subagents only, from filename agent-<hex>.jsonl
      agent_meta: AgentMeta | None

  def discover(root: Path) -> Iterator[DiscoveredFile]: ...
  ```
- `conftest.py` gains `fixture_tree(tmp_path) -> Path`. PINNED CONTRACT (later tasks hardcode these): project slugs `-Users-x-proj` (2 sessions) and `-Users-x-proj2` (1 session); main session uuids `11111111-...-111111111111`, `22222222-...-222222222222`, `33333333-...-333333333333`; every main file contains at least one user + one assistant line; session 1 additionally contains one `ai-title` line and one `file-history-snapshot` line (Task 3 fixtures provide `make_thin_meta_line("ai-title")` and `make_snapshot_line()`). Builds `<root>/<slug>/<uuid>.jsonl`, one `<uuid>/subagents/agent-abc123.jsonl` + `agent-abc123.meta.json` (tool_use_id `toolu_fixture01`, agent_type `Explore`), one `<uuid>.jsonl.bak-1720000000` whose content = the first 2 lines of its main file (an older copy — dedup will skip every bak line). Returns root. Exports `TOTAL_FIXTURE_LINES` = count of unique captured lines (main + subagent files; bak contributes 0 by design). Every later test uses this.

- [ ] **Step 1: Write failing tests**

```python
def test_discovers_main_subagent_backup(fixture_tree):
    found = list(discover(fixture_tree))
    kinds = sorted(f.kind for f in found)
    assert kinds == ["backup", "main", "main", "main", "subagent"]


def test_subagent_carries_meta_and_parent_session(fixture_tree):
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_hex_id == "abc123"
    assert sub.agent_meta.tool_use_id == "toolu_fixture01"
    assert sub.session_uuid in {f.session_uuid for f in discover(fixture_tree) if f.kind == "main"}


def test_missing_meta_json_tolerated(fixture_tree):
    (next(fixture_tree.glob("*/*/subagents/*.meta.json"))).unlink()
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_meta is None


def test_backup_ties_to_same_session(fixture_tree):
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    assert bak.session_uuid in {f.session_uuid for f in discover(fixture_tree) if f.kind == "main"}
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_discovery.py -q` — FAIL (ImportError)

- [ ] **Step 3: Implement** — walk one level of project dirs; main = `<root>/<slug>/<uuid>.jsonl` where stem parses as UUID; backup = `<uuid>.jsonl.bak-*` (session uuid = stem before `.jsonl`); subagent = `<slug>/<session-uuid>/subagents/agent-<hex>.jsonl`, meta from sibling `.meta.json` via `json.loads` (missing/corrupt → `agent_meta=None`, never raise). Skip non-matching files silently. Sort output deterministically (path).

- [ ] **Step 4: Run tests** — Expected: 4 passed

- [ ] **Step 5: Stage** — `git add server/src/introspect/ingest server/tests`

---

### Task 5: Tail reader (byte-offset, partial-line safe)

**Files:**
- Create: `server/src/introspect/ingest/reader.py`
- Test: `server/tests/test_reader.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class RawLine:
      data: bytes        # exact bytes INCLUDING trailing \n when present
      start_offset: int
      end_offset: int    # start of next line / new checkpoint candidate

  def read_complete_lines(path: Path, from_offset: int = 0) -> Iterator[RawLine]:
      """Yields complete lines only. A trailing chunk without \n is NOT yielded
      unless it is at true EOF and the file ends without a newline — then it IS
      yielded (final-line-no-newline case). Distinguish via: a line missing \n
      is yielded only when file size == its end_offset at open time."""
  ```

- [ ] **Step 1: Write failing tests**

```python
def test_reads_lines_with_offsets(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}\n')
    lines = list(read_complete_lines(p))
    assert [l.data for l in lines] == [b'{"a":1}\n', b'{"b":2}\n']
    assert lines[1].start_offset == 8 and lines[1].end_offset == 16


def test_resumes_from_offset(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}\n')
    assert [l.data for l in read_complete_lines(p, from_offset=8)] == [b'{"b":2}\n']


def test_final_line_without_newline_is_yielded(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_bytes(b'{"a":1}\n{"b":2}')
    assert [l.data for l in read_complete_lines(p)][-1] == b'{"b":2}'


def test_streams_large_line(tmp_path):
    p = tmp_path / "f.jsonl"
    big = b'{"x":"' + b"y" * 600_000 + b'"}\n'
    p.write_bytes(big)
    (line,) = list(read_complete_lines(p))
    assert line.end_offset == len(big)
```

- [ ] **Step 2: Run to verify failure** — FAIL (ImportError)

- [ ] **Step 3: Implement** — open `"rb"`, `seek(from_offset)`, stat size once at open; iterate with `readline()` (unbounded is fine — it buffers internally); track offsets arithmetically (never `tell()` inside iteration of a buffered reader — compute `start + len(data)`).

- [ ] **Step 4: Run tests** — 4 passed. **Step 5: Stage** — `git add server/src/introspect/ingest/reader.py server/tests/test_reader.py`

---

### Task 6: Capture — fresh ingest, checkpoints, idempotency, tail append

**Files:**
- Create: `server/src/introspect/ingest/capture.py`
- Test: `server/tests/test_capture.py`

**Interfaces:**
- Consumes: `discover()`, `read_complete_lines()`, `parse_line()`, ORM models, `session_factory`.
- Produces:
  ```python
  @dataclass
  class CaptureStats:
      records_added: int
      records_skipped_duplicate: int
      anomalies: int

  def capture_file(db: Session, f: DiscoveredFile) -> CaptureStats:
      """Ensures Project/Session(session row)/Transcript/SourceFile rows exist
      (get-or-create), then ingests new complete lines from the checkpoint.
      Per line: DEDUP CHECK first (same-transcript skip — an incoming line whose
      (record_uuid, line_sha256) already exists in this transcript is skipped,
      records_skipped_duplicate += 1; uuid-less lines skip on an existing
      (transcript, line_sha256, line_number) from a DIFFERENT source_file).
      Then insert RawRecord (raw_line bytes, line_sha256, line_number,
      byte_offset, record_type/uuid/version from parse_line).
      TRANSACTION SPLIT (capture is sacred): raw_records + checkpoint/prefix_hash
      advance COMMIT FIRST in their own chunk transaction. Interpretation runs
      AFTER that commit, in a separate transaction per chunk: hand each ParseResult
      to interpret.apply() [Task 8; until then a no-op stub named
      `apply(db, parse_result, raw_record)` in interpret.py created here] inside
      per-record try/except — an apply() exception writes a ParseAnomaly (kind
      'interpret_failure', severity error) + parse_status='anomaly' and continues;
      interpretation can NEVER roll back capture.
      Anomalies from parse_line -> ParseAnomaly rows linked to the RawRecord.
      A whitespace-only raw line is captured but anomaly-graded 'info' (torn-write
      residue), not error. Transcript rows get agent_type/agent_description/
      parent_tool_use_id from DiscoveredFile.agent_meta when present.
      is_primary=True for a transcript's first non-backup file, False for kind
      'backup' (divergence generations re-point it — Task 7).
      Commits in chunks of 500 lines; SourceFile.byte_offset_checkpoint,
      last_size, prefix_hash advance only inside the same commit as their lines."""
  ```
- `prefix_hash`: running sha256 of all ingested bytes, stored hex; persisted per chunk alongside checkpoint (recompute continuation by hashing existing prefix on resume — acceptable at current scale, per spec).

- [ ] **Step 1: Write failing tests**

```python
def _capture_all(db, root):
    return {f.path: capture_file(db, f) for f in discover(root)}


def test_fresh_ingest_counts(db_session, fixture_tree):
    stats = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats.values()) == TOTAL_FIXTURE_LINES
    assert db_session.query(RawRecord).count() == TOTAL_FIXTURE_LINES


def test_rerun_is_noop(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    stats2 = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats2.values()) == 0


def test_tail_append_ingests_only_new(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(make_user_line(text="post-checkpoint prompt"))
    stats2 = _capture_all(db_session, fixture_tree)
    assert sum(s.records_added for s in stats2.values()) == 1


def test_partial_trailing_line_not_captured_then_captured(db_session, fixture_tree):
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with main.path.open("ab") as fh:
        fh.write(b'{"type":"user"')          # torn write, no newline
    _capture_all(db_session, fixture_tree)
    n1 = db_session.query(RawRecord).count()
    with main.path.open("ab") as fh:
        fh.write(b',"message":{"role":"user","content":"x"}}\n')
    _capture_all(db_session, fixture_tree)
    assert db_session.query(RawRecord).count() == n1 + 1


def test_raw_bytes_stored_exactly(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    src = main.path.read_bytes()
    rows = (db_session.query(RawRecord).join(SourceFile)
            .filter(SourceFile.path == str(main.path))
            .order_by(RawRecord.line_number).all())
    assert b"".join(r.raw_line for r in rows) == src
```

`conftest.py` additions: `db_session` fixture (tmp engine + `upgrade_to_head` + session), `TOTAL_FIXTURE_LINES` exported from the tree builder.

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** per interface docstring (get-or-create keyed: Project by dir_slug; ChatSession by session_uuid; Transcript by (session, kind, agent_hex_id); SourceFile by path). Final-line contract: the reader yields a no-newline chunk only at true EOF, where a torn write is indistinguishable from a genuine final line. Capture treats a yielded no-newline line as capturable **only if it parses as JSON**; otherwise the checkpoint stays before it (torn write in progress — retried next run). Byte-faithfulness is preserved either way because export is pure concatenation of captured bytes.

- [ ] **Step 4: Run tests** — all pass. **Step 5: Stage** — `git add server/src/introspect/ingest/capture.py server/src/introspect/ingest/interpret.py server/tests`

---

### Task 7: Capture — dedup, divergence, gone-at-source

**Files:**
- Modify: `server/src/introspect/ingest/capture.py`
- Test: `server/tests/test_capture_integrity.py`

**Interfaces:**
- Produces additions:
  ```python
  def detect_gone(db: Session, discovered: list[DiscoveredFile]) -> int:
      """SourceFiles with status 'active' whose path is absent from discovered
      and does not exist on disk -> status 'gone_at_source' + gone_detected_at.
      Returns count flipped. Never touches rows below."""
  ```
- Dedup rule inside `capture_file` (restored/copied source case — spec §6.5): before inserting a RawRecord, if the *transcript* already has a record with same `record_uuid` and same `line_sha256` → skip (increment `records_skipped_duplicate`). Same `record_uuid`, different hash → insert + `error` anomaly kind `uuid_content_conflict`. Uuid-less thin records: skip when (transcript, line_sha256, line_number) matches an existing row from another file.
- Divergence rule: on visiting a known SourceFile, if `size < checkpoint` OR sha256(first `checkpoint` bytes) != stored `prefix_hash` → mark old row `diverged` (anomaly kind `source_diverged`, severity error), create NEW SourceFile row (same path allowed — drop unique on path to unique on (path, status!='diverged') is messy; instead: unique constraint becomes `(path, generation)` with `generation int default 0`, new row = generation+1, `is_primary` moves to newest), full re-ingest that BYPASSES dedup entirely — every line of the rewritten file is written to the new generation source_file so the new primary is complete and exportable. Dedup's job is restored/copied sources at a DIFFERENT path; the discriminator is path identity: same path + changed prefix => divergence (bypass, full re-ingest), different path => dedup-skip. (Opus review B1: routing divergence through dedup would leave the new generation sparse and break reconstruction.)
  **Schema delta:** add `generation` column to SourceFile in migration 0001 (plan-time change, not a second migration — 0001 hasn't shipped).

- [ ] **Step 1: Write failing tests**

```python
def test_restored_copy_dedups(db_session, fixture_tree, tmp_path):
    _capture_all(db_session, fixture_tree)
    n = db_session.query(RawRecord).count()
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    restored_root = tmp_path / "restored"
    dst = restored_root / main.path.parent.name / main.path.name
    dst.parent.mkdir(parents=True); dst.write_bytes(main.path.read_bytes())
    for f in discover(restored_root):
        s = capture_file(db_session, f)
    assert db_session.query(RawRecord).count() == n
    assert s.records_skipped_duplicate > 0


def test_divergence_detected_and_regenerated(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.write_bytes(b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-new1"}\n' + content[content.index(b"\n") + 1:])
    _capture_all(db_session, fixture_tree)
    gens = db_session.query(SourceFile).filter_by(path=str(main.path)).all()
    assert {g.status for g in gens} == {"diverged", "active"}
    assert next(g for g in gens if g.status == "active").is_primary


def test_gone_at_source(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    victim = next(f for f in discover(fixture_tree) if f.kind == "main")
    victim.path.unlink()
    remaining = list(discover(fixture_tree))
    flipped = detect_gone(db_session, remaining)
    assert flipped == 1
    row = db_session.query(SourceFile).filter_by(path=str(victim.path)).one()
    assert row.status == "gone_at_source" and row.gone_detected_at is not None
    assert db_session.query(RawRecord).filter_by(source_file_id=row.id).count() > 0
```

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** per rules above (and add `generation` to models + 0001).
- [ ] **Step 4: Run full suite** — `uv run pytest -q` — all pass. **Step 5: Stage** — `git add server/src server/alembic server/tests`

---

### Task 8: Interpretation

**Files:**
- Modify: `server/src/introspect/ingest/interpret.py` (replace Task 6 stub)
- Test: `server/tests/test_interpret.py`

**Interfaces:**
- Consumes: `ParseResult` (+ `NormalizedBlock` via `record.blocks()`), ORM models.
- Produces:
  ```python
  def apply(db: Session, pr: ParseResult, raw: RawRecord) -> None:
      """status 'anomaly' or record None -> mark raw.parse_status, return.
      Conversational types (user/assistant/system/attachment):
        Message row (timestamp parsed ISO->aware-UTC datetime; None tolerated)
        + ContentBlock rows from record.blocks() (block_index = position)
        + TokenUsage when assistant usage present.
      Thin metas -> SessionEvent(event_kind=record_type, payload=model_dump).
        ai-title/custom-title ALSO update Session.ai_title/custom_title.
      Every path: raw.parsed_with_schema_version = SCHEMA_VERSION;
      Session.started_at/last_activity_at min/max-folded from message timestamps."""
  ```

- [ ] **Step 1: Write failing tests**

```python
def test_user_message_and_block(db_session, ingested_user_raw):
    msg = db_session.query(Message).one()
    assert msg.type == "user" and msg.record_uuid
    blocks = db_session.query(ContentBlock).filter_by(message_id=msg.id).all()
    assert blocks[0].block_kind == "text" and blocks[0].text_content == "hello world"


def test_assistant_thinking_block_kind_no_text(db_session, ingested_assistant_raw):
    kinds = {b.block_kind for b in db_session.query(ContentBlock).all()}
    assert "thinking" in kinds
    tb = db_session.query(ContentBlock).filter_by(block_kind="thinking").one()
    assert tb.text_content in (None, "")          # CLI never persists thinking text


def test_usage_row(db_session, ingested_assistant_raw):
    assert db_session.query(TokenUsage).count() == 1


def test_title_event_updates_session_cache(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    s = db_session.query(ChatSession).filter(ChatSession.ai_title.isnot(None)).first()
    assert s is not None
    assert db_session.query(SessionEvent).filter_by(event_kind="ai-title").count() >= 1


def test_session_time_bounds_folded(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    s = db_session.query(ChatSession).first()
    assert s.started_at <= s.last_activity_at


def test_file_history_snapshot_archive_only(db_session, ingested_snapshot_raw):
    assert db_session.query(Message).count() == 0
    assert db_session.query(SessionEvent).filter_by(event_kind="file-history-snapshot").count() == 1
```

(`ingested_*_raw` = small conftest fixtures that capture a single synthetic line through `capture_file`.)

Additional required tests (Opus review M2 — defeat identity-map masking — and resolved_cwd):

```python
def test_time_fold_across_fresh_engine(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    engine = get_engine(dbp); upgrade_to_head(engine)
    with session_factory(engine)() as db:
        for f in discover(fixture_tree):
            capture_file(db, f)
        db.commit()
    engine.dispose()
    # Fresh engine + fresh session: DB-loaded datetimes must still compare/fold
    engine2 = get_engine(dbp)
    with session_factory(engine2)() as db2:
        main = next(f for f in discover(fixture_tree) if f.kind == "main")
        with main.path.open("ab") as fh:
            fh.write(make_user_line(text="later prompt"))
        capture_file(db2, next(f for f in discover(fixture_tree) if f.path == main.path))
        db2.commit()
        s = db2.query(ChatSession).filter_by(session_uuid=main.session_uuid).one()
        assert s.started_at.tzinfo is not None and s.started_at <= s.last_activity_at


def test_project_resolved_cwd_populated(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    assert db_session.query(Project).filter(Project.resolved_cwd.isnot(None)).count() >= 1
```
`apply()` also sets `Project.resolved_cwd` from the first envelope `cwd` seen when it is NULL.

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** per docstring. **Step 4: Full suite** — pass. **Step 5: Stage** — `git add server/src/introspect/ingest/interpret.py server/tests`

---

### Task 9: Reparse

**Files:**
- Create: `server/src/introspect/ingest/reparse.py`
- Test: `server/tests/test_reparse.py`

**Interfaces:**
- Consumes: `parse_line()`, `interpret.apply()`, models.
- Produces:
  ```python
  @dataclass
  class ReparseStats:
      records_reparsed: int
      anomalies_before: int
      anomalies_after: int

  def reparse_all(db: Session) -> ReparseStats:
      """Deletes ALL interpretation rows in FK-safe child-first order:
      content_blocks, token_usage, THEN messages; session_events independently
      (Opus review M3). Deletes ONLY interpretation-kind parse_anomalies
      (invalid_json, unknown_record_type, unknown_field, validation_error,
      interpret_failure) — capture-phase integrity anomalies
      (uuid_content_conflict, source_diverged, file_ingest_failure) are history
      reparse cannot regenerate and MUST survive (Opus review M4). Resets
      ChatSession title/time caches, then re-runs parse_line()+apply() over every
      raw_records row ordered
      by (source_file_id, line_number), in chunks of 500. raw_line bytes are the
      only input — source files not touched. Updates parsed_with_schema_version."""
  ```

- [ ] **Step 1: Write failing tests**

```python
def test_reparse_rebuilds_identical_interpretation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    before = {
        "messages": db_session.query(Message).count(),
        "blocks": db_session.query(ContentBlock).count(),
        "events": db_session.query(SessionEvent).count(),
    }
    stats = reparse_all(db_session)
    after = {
        "messages": db_session.query(Message).count(),
        "blocks": db_session.query(ContentBlock).count(),
        "events": db_session.query(SessionEvent).count(),
    }
    assert before == after and stats.records_reparsed > 0


def test_reparse_needs_no_source_files(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    import shutil; shutil.rmtree(fixture_tree)
    stats = reparse_all(db_session)
    assert stats.records_reparsed > 0


def test_reparse_updates_schema_version_stamp(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    db_session.query(RawRecord).update({"parsed_with_schema_version": "introspect-schema/0"})
    reparse_all(db_session)
    versions = {v for (v,) in db_session.query(RawRecord.parsed_with_schema_version).distinct()}
    assert versions == {"introspect-schema/1"}
```

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** per docstring. **Step 4: Full suite** — pass. **Step 5: Stage** — `git add server/src/introspect/ingest/reparse.py server/tests/test_reparse.py`

---

### Task 10: Export — byte-faithful reconstruction (THE FLAGSHIP)

**Files:**
- Create: `server/src/introspect/export.py`
- Test: `server/tests/test_export_roundtrip.py`

**Interfaces:**
- Consumes: models only (reads `raw_records` via primary SourceFile).
- Produces:
  ```python
  def export_transcript(db: Session, session_uuid: str, kind: str = "main",
                        agent_hex_id: str | None = None) -> bytes:
      """Concatenation of raw_line for the transcript's primary source file,
      ordered by line_number. Raises SessionNotFoundError / TranscriptNotFoundError."""

  def export_session_to(db: Session, session_uuid: str, out_path: Path) -> int:
      """Writes main-transcript bytes; returns byte count."""
  ```

- [ ] **Step 1: Write THE test**

```python
def test_roundtrip_every_fixture_file_byte_identical(db_session, fixture_tree):
    """The archive guarantee: import -> export == original bytes, every file."""
    _capture_all(db_session, fixture_tree)
    for f in discover(fixture_tree):
        if f.kind == "backup":
            continue          # backups are non-primary by design
        exported = export_transcript(db_session, f.session_uuid, f.kind, f.agent_hex_id)
        assert exported == f.path.read_bytes(), f.path


def test_roundtrip_survives_source_deletion(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    original = main.path.read_bytes()
    main.path.unlink()
    detect_gone(db_session, list(discover(fixture_tree)))
    assert export_transcript(db_session, main.session_uuid) == original


def test_roundtrip_no_trailing_newline(db_session, tmp_path):
    root = tmp_path / "r"; slug = root / "-Users-x-proj"; slug.mkdir(parents=True)
    p = slug / "aaaaaaaa-1111-2222-3333-444444444444.jsonl"
    content = make_user_line(text="one") + make_user_line(text="two").rstrip(b"\n")
    p.write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    assert export_transcript(db_session, "aaaaaaaa-1111-2222-3333-444444444444") == content


def test_export_unknown_session_raises(db_session):
    with pytest.raises(SessionNotFoundError):
        export_transcript(db_session, "no-such-uuid")
```

Additional required tests (Opus review B1 + M5):

```python
def test_roundtrip_after_divergence_exports_new_generation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    rewritten = b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-new1"}\n' + content[content.index(b"\n") + 1:]
    main.path.write_bytes(rewritten)
    _capture_all(db_session, fixture_tree)
    assert export_transcript(db_session, main.session_uuid) == rewritten


def test_bak_only_transcript_still_exports(db_session, tmp_path):
    root = tmp_path / "r"; slug = root / "-Users-x-proj"; slug.mkdir(parents=True)
    content = make_user_line(text="only the backup survived")
    (slug / "cccccccc-1111-2222-3333-444444444444.jsonl.bak-1700000000").write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    assert export_transcript(db_session, "cccccccc-1111-2222-3333-444444444444") == content
```

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** (select transcript, its `is_primary` SourceFile — for `diverged` history the active generation; when NO source_file is primary — e.g. bak-only transcripts — fall back to the most-complete source_file by record count, Opus review M5; stream-concat ordered raw_line). **Step 4: Full suite** — pass. **Step 5: Stage** — `git add server/src/introspect/export.py server/tests/test_export_roundtrip.py`

---

### Task 11: Orchestrator — lock, sweep, import_runs

**Files:**
- Create: `server/src/introspect/ingest/run.py`
- Test: `server/tests/test_run.py`

**Interfaces:**
- Consumes: everything prior.
- Produces:
  ```python
  @dataclass
  class ImportSummary:
      run_id: int
      files_seen: int
      records_added: int
      records_skipped_duplicate: int
      anomaly_count: int
      gone_flipped: int
      status: str            # 'ok' | 'errors' | 'already_running'

  def run_import(db_path: Path, root: Path, trigger: str = "cli") -> ImportSummary:
      """Takes the exclusive advisory lock FIRST (fcntl.flock on
      <db_dir>/import.lock, non-blocking; held -> return status
      'already_running', no ImportRun row), THEN opens engine + upgrade_to_head
      (the DB self-migrates under the lock; Opus review minor). Creates
      ImportRun('running'), sweeps any raw_records with
      parsed_with_schema_version IS NULL through interpret.apply() (self-healing
      after a crash between capture-commit and interpret-commit; Opus review M1),
      discovers, captures each file inside its own
      try/except (exception -> file-level ParseAnomaly kind 'file_ingest_failure'
      severity error + continue), runs detect_gone, finalizes ImportRun with
      totals and status ('errors' if any error-severity anomalies else 'ok')."""
  ```

- [ ] **Step 1: Write failing tests**

```python
def test_end_to_end_import(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    s = run_import(dbp, fixture_tree)
    assert s.status == "ok" and s.files_seen == 5 and s.records_added == TOTAL_FIXTURE_LINES
    s2 = run_import(dbp, fixture_tree)
    assert s2.records_added == 0


def test_lock_contention_returns_already_running(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    lock = dbp.parent / "import.lock"
    lock.touch()
    import fcntl
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        s = run_import(dbp, fixture_tree)
        assert s.status == "already_running"


def test_bad_file_does_not_halt_run(tmp_path, fixture_tree):
    # a directory posing as a jsonl file -> open() raises -> file anomaly, run continues
    evil = fixture_tree / "-Users-x-proj2" / "bbbbbbbb-1111-2222-3333-444444444444.jsonl"
    evil.mkdir(parents=True)
    dbp = tmp_path / "a.db"
    s = run_import(dbp, fixture_tree)
    assert s.status == "errors" and s.records_added == TOTAL_FIXTURE_LINES


def test_import_run_row_written(tmp_path, fixture_tree):
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    engine = get_engine(dbp)
    with session_factory(engine)() as db:
        run = db.query(ImportRun).one()
        assert run.status == "ok" and run.finished_at is not None
```

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** per docstring. **Step 4: Full suite** — pass. **Step 5: Stage** — `git add server/src/introspect/ingest/run.py server/tests/test_run.py`

---

### Task 12: CLI

**Files:**
- Create: `server/src/introspect/cli.py`
- Test: `server/tests/test_cli.py`

**Interfaces:**
- Consumes: `run_import`, `reparse_all`, `export_session_to`, config, db.
- Produces: `main(argv: list[str] | None = None) -> int` wired as console script. Subcommands:
  - `introspect import [--db PATH] [--source-root PATH]` → prints one summary line `imported files=N records=N dupes=N anomalies=N gone=N status=S`; exit 0 (ok/already_running) / 1 (errors)
  - `introspect reparse [--db PATH]` → summary line; exit 0
  - `introspect export SESSION_UUID [-o FILE] [--db PATH]` → file out (default `<uuid>.jsonl` in cwd); exit 0, or 1 with stderr message on unknown session (2 stays reserved for DB-open failure per spec §6.7)
  - `introspect status [--db PATH]` → sessions/files/records/anomaly counts + last run line; exit 0
  - fatal DB-open failures → message on stderr, exit 2

- [ ] **Step 1: Write failing tests**

```python
def test_cli_import_and_status(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["import", "--db", dbp, "--source-root", str(fixture_tree)]) == 0
    out = capsys.readouterr().out
    assert "status=ok" in out
    assert main(["status", "--db", dbp]) == 0
    assert "sessions=" in capsys.readouterr().out


def test_cli_export_roundtrip(tmp_path, fixture_tree, capsys):
    dbp = str(tmp_path / "a.db")
    main(["import", "--db", dbp, "--source-root", str(fixture_tree)])
    f = next(x for x in discover(fixture_tree) if x.kind == "main")
    out = tmp_path / "out.jsonl"
    assert main(["export", f.session_uuid, "-o", str(out), "--db", dbp]) == 0
    assert out.read_bytes() == f.path.read_bytes()


def test_cli_export_unknown_session_exit_1(tmp_path, capsys):
    dbp = str(tmp_path / "a.db")
    assert main(["export", "not-a-uuid", "--db", dbp]) == 1


def test_cli_import_with_errors_exit_1(tmp_path, fixture_tree):
    (fixture_tree / "-Users-x-proj2" / "bbbbbbbb-1111-2222-3333-444444444444.jsonl").mkdir(parents=True)
    assert main(["import", "--db", str(tmp_path / "a.db"), "--source-root", str(fixture_tree)]) == 1
```

- [ ] **Step 2: Run to verify failure** — FAIL. **Step 3: Implement** (argparse subparsers; thin — all logic lives in the modules it calls). **Step 4: Full suite + ruff** — `uv run pytest -q && uv run ruff check .` — all pass, clean. **Step 5: Stage** — `git add server/src/introspect/cli.py server/tests/test_cli.py`

---

### Task 13: First real import (operational verification — the reason this project exists)

**Files:** none created (operations, not code). Document results in `claude_notes/`.

- [ ] **Step 1: Run against reality**

Run: `cd server && uv run introspect import`
Expected: `files=~146 records=~15000 status=ok` orders of magnitude (live numbers will differ); exit 0.

- [ ] **Step 2: Status + anomaly review**

Run: `uv run introspect status`
Review anomaly counts. `info`-severity drift on unmodeled fields is expected and fine; investigate any `error`.

- [ ] **Step 3: Live byte-compare (the guarantee, proven on real data)**

```bash
S=$(ls ~/.claude/projects/-Users-donovan-projects--ai-conversation-introspection/*.jsonl | head -1)
UUID=$(basename "$S" .jsonl)
uv run introspect export "$UUID" -o /tmp/roundtrip.jsonl
cmp "$S" /tmp/roundtrip.jsonl && echo "BYTE-IDENTICAL"
```
Expected: `BYTE-IDENTICAL`. (Today's session file keeps growing — if cmp fails on length, re-import once and re-export; content prefix must still match.)

- [ ] **Step 4: Record the capture**

Append to the session plan-log in `claude_notes/`: import counts, anomaly summary, byte-compare result, and the DB size. **The 12 survivors are now archived.** Note cron line for Donovan to register when he chooses: `*/15 * * * * <abs-path>/uv run --project <abs-path>/server introspect import`

---

## Execution notes for the orchestrator

- Tasks 1→2→3 are strictly sequential. 4 and 5 are independent of each other (both need 3). 6 needs 4+5; 7 needs 6; 8 needs 6 (stub replacement); 9 needs 8; 10 needs 7; 11 needs 7+8+9+10... run 6-12 sequentially — the files overlap enough that parallel dispatch buys races, not time. 13 is manual-ish and last.
- Reviewer calibration (per practices): full two-stage review for Tasks 3, 6, 7, 10 (contract-bearing / regression-risky); single code-review for 2, 8, 11, 12; spot-check diff for 1, 4, 5, 9.
- Every implementer writes a short self-check note in its report: what it changed, what surprised it, any plan deviation (deviations that contradict the spec → stop and surface).
