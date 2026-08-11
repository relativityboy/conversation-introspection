# Pretty-Printed JSONL Compat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `docs/superpowers/specs/2026-08-05-pretty-jsonl-compat-design.md` — tolerant capture of pretty-printed multi-line JSON records, an `introspect recapture` healing command gated by byte reconciliation, and `introspect-schema/5` — ending at an archive anomaly count of 5.

**Architecture:** Record reassembly lives in the reader layer (`ingest/reader.py`) as a unit iterator wrapping the existing line iterator; capture consumes units instead of lines with byte-identical fast-path behavior. `recapture` is a sibling of `reparse` (same lock, same delete-discipline, same savepoint interpretation) plus a capture-layer swap that only proceeds when stored bytes reconcile exactly. Schema/5 follows the schema/4 declaration idiom verbatim.

**Tech Stack:** Python 3.12 + SQLAlchemy + Alembic + pytest (server only; zero web changes). No new dependencies.

## Global Constraints

- **Capture is sacred:** `raw_line` stays exact bytes (including trailing `\n` and, for reassembled units, internal `\n`s); `prefix_hash` remains sha256 of every byte read in order — reassembly changes record BOUNDARIES, never bytes, so hashes and checkpoints are unchanged by construction.
- **No source-file writes, ever.** `recapture` reads sources, writes only the archive.
- **Fast path byte-for-byte:** a compact file must produce IDENTICAL RawRecord rows (line_number, byte_offset, raw_line, line_sha256) to the current reader — pinned by a golden test.
- **Reassembly bounds (spec §2, exact):** cap 1,000 lines or 1 MiB per record, whichever first; opener heuristic (first non-whitespace byte `{`); depth<0 aborts; EOF with an open buffer defers (torn-write rule at unit granularity — nothing emitted, checkpoint stays before the buffer).
- **Recapture gate (spec §3, exact):** swap only if `concat(new unit bytes) == concat(stored raw_line ORDER BY line_number)` for the file's captured prefix; on mismatch: refuse, diagnose, change nothing, exit non-zero.
- **Never delete capture-phase bookkeeping anomalies** (`source_diverged`, `source_reappeared`) — reparse's own rule, inherited by recapture.
- **Fixture law (conftest.py header):** the existing fixture tree is a PINNED CONTRACT — do not add/remove lines in `_SESSION_*_LINES` or touch `TOTAL_FIXTURE_LINES`. All new fixtures are NEW files/sessions with NEW uuids.
- **Tests:** `cd server && uv run pytest tests/<file> -q`; full suite before each commit: `cd server && uv run pytest -q`; lint: `uv run ruff check .`.
- **Commits:** one per task; `--author` names the executing tier (e.g. `--author="Claude (Sonnet 5) <noreply@anthropic.com>"`); explicit pathspecs only — three docs files sit intentionally staged (two specs + one plan); a bare `git commit` has swept them before; verify `git status --porcelain | grep -v '^??'` shows only those three `A ` lines after every commit.
- **Minion economics:** T1, T6, T7 → Haiku; T2, T3, T4, T5 → Sonnet. Escalate only after a cheap attempt fails.
- **Schema/5 field list (exact, census-verified):** `interruptedByShutdown`, `source`, `userFeedback`, `isAbortedMidStream`, `pendingWorkflowCount`, `logicalParentUuid`, `compactMetadata`, `isVisibleInTranscriptOnly`, `isCompactSummary` on `UserRecord`/`AssistantRecord` per Task 6's placement table.

---

### Task 1: Migration 0007 — `raw_records.reassembled`

**Files:**
- Create: `server/alembic/versions/0007_raw_records_reassembled.py`
- Modify: `server/src/introspect/models.py` (RawRecord, after `parse_status`)
- Test: `server/tests/test_migration_0007.py`

**Interfaces:**
- Consumes: migration 0006 (`revision='0006'`) as `down_revision`; the binding-contract test idiom of `server/tests/test_migration_0006.py`.
- Produces: `RawRecord.reassembled: Mapped[bool]` (server default false) — Task 3 sets it True for multi-line units; Task 5 copies it through recapture.

- [ ] **Step 1: Write the failing test** (clone `test_migration_0006.py`'s structure — upgrade head then inspect, downgrade then inspect):

```python
def test_0007_adds_reassembled_column(tmp_path: Path) -> None:
    """Capture metadata for spec §2's provenance marker: reassembled records are
    always distinguishable from native single-line captures."""
    engine = _fresh_db(tmp_path)  # same helper idiom as test_migration_0006
    cols = {c["name"]: c for c in inspect(engine).get_columns("raw_records")}
    assert "reassembled" in cols
    assert cols["reassembled"]["nullable"] is False
```

Add the downgrade assertion the 0006 test carries (column absent after `command.downgrade(cfg, "0006")`).

- [ ] **Step 2: Run — expect FAIL** (`reassembled` not present): `cd server && uv run pytest tests/test_migration_0007.py -q`
- [ ] **Step 3: Implement.** Migration 0007 (chain `down_revision = "0007"`'s parent = `"0006"`; docstring: the spec §2 provenance marker — census must always distinguish native from reassembled records): `op.add_column("raw_records", sa.Column("reassembled", sa.Boolean(), nullable=False, server_default=sa.false()))`; downgrade drops it. Model: `reassembled: Mapped[bool] = mapped_column(default=False, server_default=sa.false())` placed after `parse_status` with a one-line comment citing spec §2.
- [ ] **Step 4: Run — expect PASS**, then full suite (fixture DBs migrate on creation — everything must stay green): `uv run pytest -q`
- [ ] **Step 5: Ruff, commit**

```bash
git add server/alembic/versions/0007_raw_records_reassembled.py server/src/introspect/models.py server/tests/test_migration_0007.py
git commit --author="Claude (Haiku 4.5) <noreply@anthropic.com>" -m "server: raw_records.reassembled — capture-provenance marker for multi-line units (compat spec §2)"
```

---

### Task 2: The unit reader — brace-balanced reassembly in `ingest/reader.py`

**Files:**
- Modify: `server/src/introspect/ingest/reader.py`
- Test: `server/tests/test_reader_units.py` (new)

**Interfaces:**
- Consumes: `RawLine` / `read_complete_lines` (unchanged).
- Produces (Task 3 consumes exactly this):

```python
@dataclass
class RawUnit:
    data: bytes        # exact consumed bytes: one line, or N whole lines incl. internal \n
    start_offset: int
    end_offset: int
    line_span: int     # file lines consumed (1 for native records)
    reassembled: bool  # True iff line_span > 1


def read_complete_units(path: Path, from_offset: int = 0) -> Iterator[RawUnit]: ...
```

Contract: concatenating `unit.data` over a full iteration equals concatenating `line.data` from `read_complete_lines` over the same range — reassembly moves boundaries, never bytes. A trailing torn write (newline-less non-JSON tail) is NOT yielded, same as today; additionally an EOF-open reassembly buffer is NOT yielded (deferral — spec §2's torn-write mirror).

- [ ] **Step 1: Write the failing tests.** Pure-function tests over temp files, no DB. Build inputs with `json.dumps(obj, indent=2).encode() + b"\n"` for pretty records and the compact idiom for native ones. Cases (each a real test):

```python
def _pretty(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()

def _compact(obj) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

def test_compact_file_yields_one_unit_per_line_byte_identical(tmp_path):
    lines = [_compact({"type": "user", "n": i}) for i in range(3)]
    p = tmp_path / "a.jsonl"; p.write_bytes(b"".join(lines))
    units = list(read_complete_units(p))
    assert [u.data for u in units] == lines
    assert all(u.line_span == 1 and not u.reassembled for u in units)
    # offsets identical to the line reader's
    assert [(u.start_offset, u.end_offset) for u in units] == [
        (l.start_offset, l.end_offset) for l in read_complete_lines(p)
    ]

def test_pretty_record_reassembles_to_exact_bytes(tmp_path):
    pretty = _pretty({"type": "mode", "mode": "normal", "sessionId": "s"})
    tail = _compact({"type": "user", "uuid": "u1"})
    p = tmp_path / "a.jsonl"; p.write_bytes(pretty + tail)
    units = list(read_complete_units(p))
    assert units[0].data == pretty and units[0].reassembled
    assert units[0].line_span == pretty.count(b"\n")
    assert units[1].data == tail and units[1].line_span == 1

def test_strings_containing_braces_and_escapes(tmp_path):
    # '}' at line start inside a string value; escaped quotes; nested arrays
    obj = {"type": "user", "text": "}\n} not a close\n\"quoted\"", "arr": [{"k": "{"}]}
    p = tmp_path / "a.jsonl"; p.write_bytes(_pretty(obj))
    units = list(read_complete_units(p))
    assert len(units) == 1 and json.loads(units[0].data) == obj

def test_giveup_emits_first_line_alone_and_resumes_next_line(tmp_path):
    # An opener line that never balances, followed by a valid compact record:
    garbage = b'{ "never": "closes",\n'
    good = _compact({"type": "user", "uuid": "u2"})
    filler = b'"not an opener line",\n'  # fails json? no — it IS valid JSON (a string)...
    # Use a non-JSON, non-opener line so the replay path is exercised:
    filler = b'  "type": "fragment",\n'
    p = tmp_path / "a.jsonl"; p.write_bytes(garbage + filler + good)
    units = list(read_complete_units(p))
    # garbage line reaches EOF-of-buffer? No — 'good' closes nothing; depth never returns
    # to zero, cap/EOF rules apply per implementation; the CONTRACT here:
    # every input byte comes back out, in order, and `good` is one clean unit.
    assert b"".join(u.data for u in units) == garbage + filler + good
    assert any(u.data == good and u.line_span == 1 for u in units)

def test_depth_negative_aborts_immediately(tmp_path):
    bad = b'{ "a": 1 }}\n'  # }} — json.loads fails, scan hits depth<0
    good = _compact({"type": "user", "uuid": "u3"})
    p = tmp_path / "a.jsonl"; p.write_bytes(bad + good)
    units = list(read_complete_units(p))
    assert units[0].data == bad and units[0].line_span == 1 and not units[0].reassembled
    assert units[1].data == good

def test_non_opener_failures_never_start_reassembly(tmp_path):
    frag = b'  "type": "last-prompt",\n'  # the real incident's fragment shape
    good = _compact({"type": "user", "uuid": "u4"})
    p = tmp_path / "a.jsonl"; p.write_bytes(frag + good)
    units = list(read_complete_units(p))
    assert units[0].data == frag and units[0].line_span == 1

def test_eof_open_buffer_defers_everything_from_buffer_start(tmp_path):
    good = _compact({"type": "user", "uuid": "u5"})
    partial = b'{\n  "type": "mode",\n'  # record still being written
    p = tmp_path / "a.jsonl"; p.write_bytes(good + partial)
    units = list(read_complete_units(p))
    assert [u.data for u in units] == [good]  # partial NOT emitted, no bytes invented

def test_line_cap_gives_up(tmp_path):
    opener = b'{\n' + b'  "k": "v",\n' * 1001
    p = tmp_path / "a.jsonl"; p.write_bytes(opener + _compact({"type": "user"}))
    units = list(read_complete_units(p))
    assert units[0].data == b"{\n" and units[0].line_span == 1  # first line alone, replay follows

def test_torn_write_tail_still_deferred(tmp_path):
    good = _compact({"type": "user", "uuid": "u6"})
    p = tmp_path / "a.jsonl"; p.write_bytes(good + b'{"half')  # no newline
    assert [u.data for u in list(read_complete_units(p))] == [good]
```

(The `test_giveup...` case intentionally pins only the byte-conservation + clean-tail contract, since exactly when the cap-vs-EOF rule fires there is an implementation choice; the named cases on either side pin the specific rules.)

- [ ] **Step 2: Run — expect FAIL** (`read_complete_units` undefined): `uv run pytest tests/test_reader_units.py -q`
- [ ] **Step 3: Implement in `reader.py`.** Shape:

```python
_REASSEMBLY_MAX_LINES = 1000
_REASSEMBLY_MAX_BYTES = 1024 * 1024


def _is_single_json(data: bytes) -> bool:
    try:
        json.loads(data)
        return True
    except (ValueError, TypeError):
        return False


def _looks_like_opener(data: bytes) -> bool:
    stripped = data.lstrip()
    return stripped.startswith(b"{")


def _scan_balance(data: bytes, depth: int, in_string: bool, escaped: bool):
    """String-and-escape-aware brace/bracket depth over one chunk; returns
    (depth, in_string, escaped, went_negative)."""
    went_negative = False
    for b in data:
        if escaped:
            escaped = False
        elif in_string:
            if b == 0x5C:      # backslash
                escaped = True
            elif b == 0x22:    # quote
                in_string = False
        elif b == 0x22:
            in_string = True
        elif b in (0x7B, 0x5B):   # { [
            depth += 1
        elif b in (0x7D, 0x5D):   # } ]
            depth -= 1
            if depth < 0:
                went_negative = True
    return depth, in_string, escaped, went_negative


def read_complete_units(path: Path, from_offset: int = 0) -> Iterator[RawUnit]:
    pending: deque[RawLine] = deque()

    def next_line(lines) -> RawLine | None:
        if pending:
            return pending.popleft()
        return next(lines, None)

    lines = read_complete_lines(path, from_offset=from_offset)
    while True:
        line = next_line(lines)
        if line is None:
            return
        if _is_single_json(line.data):
            yield RawUnit(line.data, line.start_offset, line.end_offset, 1, False)
            continue
        if not line.data.endswith(b"\n"):
            return  # torn tail: same deferral as read_complete_lines' contract
        if not _looks_like_opener(line.data):
            yield RawUnit(line.data, line.start_offset, line.end_offset, 1, False)
            continue
        # Reassembly attempt.
        buf = [line]
        depth, in_str, esc, neg = _scan_balance(line.data, 0, False, False)
        total = len(line.data)
        failed = neg or depth == 0  # depth==0 here means balanced-but-invalid JSON
        while not failed:
            nxt = next_line(lines)
            if nxt is None:
                return  # EOF with open buffer: defer everything (torn-write mirror)
            buf.append(nxt)
            total += len(nxt.data)
            depth, in_str, esc, neg = _scan_balance(nxt.data, depth, in_str, esc)
            if neg or len(buf) > _REASSEMBLY_MAX_LINES or total > _REASSEMBLY_MAX_BYTES:
                failed = True
            elif depth == 0:
                if not nxt.data.endswith(b"\n") and ...:  # trailing-EOF unit is fine: bytes are complete
                    pass
                joined = b"".join(l.data for l in buf)
                if _is_single_json(joined):
                    yield RawUnit(joined, buf[0].start_offset, buf[-1].end_offset, len(buf), True)
                    buf = []
                    break
                failed = True
        if failed and buf:
            first, *rest = buf
            pending.extendleft(reversed(rest))  # replay the remainder as fresh starts
            yield RawUnit(first.data, first.start_offset, first.end_offset, 1, False)
```

The excerpt is the architecture, not gospel — the implementer owns making every Step-1 test pass with clean control flow (in particular the balanced-but-invalid and give-up paths; delete the stray `...` conditional if the final shape doesn't need it). `RawUnit` and both helpers get docstrings citing spec §2; `read_complete_lines` and `RawLine` are untouched.

- [ ] **Step 4: Run — expect PASS**, then full suite: `uv run pytest -q`
- [ ] **Step 5: Ruff, commit**

```bash
git add server/src/introspect/ingest/reader.py server/tests/test_reader_units.py
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: read_complete_units — brace-balanced reassembly of pretty-printed records, bounded, byte-conserving (compat spec §2)"
```

---

### Task 3: Capture consumes units

**Files:**
- Modify: `server/src/introspect/ingest/capture.py` (`capture_file` loop :102-124, `_capture_chunk` :160-252)
- Test: `server/tests/test_capture_reassembly.py` (new) + one golden test added to `server/tests/test_capture.py`

**Interfaces:**
- Consumes: `read_complete_units` / `RawUnit` (Task 2), `RawRecord.reassembled` (Task 1).
- Produces: capture behavior Tasks 4–5 rely on — a reassembled unit becomes ONE RawRecord with `line_number` = ordinal of its FIRST file line, `byte_offset` = unit start, `raw_line` = full unit bytes, `line_sha256` = sha256 of unit bytes, `reassembled=True`; `file_line_number` advances by `line_span` (gaps in stored line_number stay legal — dedup already creates them); prefix hash unchanged (same bytes, same order).

- [ ] **Step 1: Golden test FIRST (before touching capture).** In `test_capture.py`, add a pin of the CURRENT compact-path output so the swap is provably byte-identical:

```python
def test_capture_compact_rows_golden(db_session, fixture_tree):
    """Fast-path conservation pin (compat spec §2): switching capture to unit
    iteration must not move a single byte, boundary, or hash for compact files."""
    _capture_all(db_session, fixture_tree)
    rows = (
        db_session.query(
            RawRecord.line_number, RawRecord.byte_offset,
            RawRecord.line_sha256, RawRecord.reassembled,
        )
        .join(SourceFile)
        .filter(SourceFile.path.like(f"%{SESSION_UUID_1}.jsonl"))
        .order_by(RawRecord.line_number)
        .all()
    )
    assert all(r.reassembled is False for r in rows)
    assert [r.line_number for r in rows] == list(range(1, len(rows) + 1))
    # byte_offset of row N+1 == byte_offset of row N + len(raw_line N): arithmetic pin
    raws = [r for (r,) in db_session.query(RawRecord.raw_line).join(SourceFile)
            .filter(SourceFile.path.like(f"%{SESSION_UUID_1}.jsonl"))
            .order_by(RawRecord.line_number)]
    offs = [r.byte_offset for r in rows]
    assert offs == [sum(len(x) for x in raws[:i]) for i in range(len(raws))]
```

Run it — expect PASS against the CURRENT code (it pins today's behavior; `reassembled` exists via Task 1's default).

- [ ] **Step 2: Write the failing reassembly tests** (`test_capture_reassembly.py`; use the `_ingest_single_line`-style pattern with a NEW session uuid, never the pinned tree):

```python
PRETTY_SESSION_UUID = "7a7a7a7a-0000-4000-8000-000000000007"

def _pretty_line(builder_bytes: bytes) -> bytes:
    return (json.dumps(json.loads(builder_bytes), indent=2, ensure_ascii=False) + "\n").encode()

def _write_and_capture(db, tmp_path, payload: bytes):
    proj = tmp_path / "pretty" / "-Users-x-pretty"
    proj.mkdir(parents=True)
    (proj / f"{PRETTY_SESSION_UUID}.jsonl").write_bytes(payload)
    for f in discover(tmp_path / "pretty"):
        capture_file(db, f)
    db.commit()

def test_pretty_head_compact_tail_captures_clean(db_session, tmp_path):
    pretty = _pretty_line(make_user_line(text="hand edited", sessionId=PRETTY_SESSION_UUID))
    compact = make_assistant_line(text="native", sessionId=PRETTY_SESSION_UUID)
    _write_and_capture(db_session, tmp_path, pretty + compact)
    recs = db_session.query(RawRecord).order_by(RawRecord.line_number).all()
    assert [r.reassembled for r in recs] == [True, False]
    assert recs[0].raw_line == pretty and recs[0].line_number == 1
    assert recs[1].line_number == pretty.count(b"\n") + 1  # file-position ordinal
    assert db_session.query(ParseAnomaly).count() == 0
    assert db_session.query(Message).count() == 2  # both interpreted

def test_incremental_append_after_pretty_head(db_session, tmp_path):
    # capture pretty head; then append a compact record and re-capture: prefix model holds
    ...  # write head, capture, append tail bytes to the same file, discover+capture again;
         # assert no divergence (SourceFile.generation == 1, status == 'active'),
         # tail captured as one new record, prefix_hash matches sha256(full file bytes)

def test_unbalanced_giveup_matches_per_line_anomalies(db_session, tmp_path):
    # opener that never closes + fragment lines: capture must record them as
    # per-line invalid_json anomalies exactly like today (spec §2 give-up)
    ...
```

(Write the two `...` tests in full — their contracts are stated in the comments; `_resume_prefix_state`'s hash is over raw file bytes so the incremental assertion can compute `hashlib.sha256(full_bytes).hexdigest()` directly.)

- [ ] **Step 3: Run — expect FAIL** (capture still per-line: two+ anomaly rows appear for the pretty record, `reassembled` never True): `uv run pytest tests/test_capture_reassembly.py -q`
- [ ] **Step 4: Implement.** In `capture_file`: iterate `read_complete_units(f.path, from_offset=...)` instead of `read_complete_lines`; the torn-tail guard (`not line.data.endswith(b"\n") and not _is_json(...)`) collapses into the reader's own deferral (units never arrive torn — delete the in-loop check, keep the comment moved to the reader). In `_capture_chunk`: parameter renames line→unit; `file_line_number += unit.line_span` REPLACES `+= 1`, with the RawRecord getting the PRE-increment ordinal + 1 (first line of the unit — keep the existing gap-comment and extend it: reassembled units consume span ordinals); `reassembled=unit.reassembled` on the RawRecord constructor; sha/dedup/running-hash operate on `unit.data` unchanged. `CHUNK_SIZE` batching now counts units — fine (comment it).
- [ ] **Step 5: Run — expect PASS on new tests AND the golden pin AND the whole suite** (the roundtrip tests are the real gate): `uv run pytest -q`
- [ ] **Step 6: Ruff, commit**

```bash
git add server/src/introspect/ingest/capture.py server/tests/test_capture_reassembly.py server/tests/test_capture.py
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: capture consumes reassembly units — pretty records land whole, fast path golden-pinned (compat spec §2)"
```

---

### Task 4: Pretty fixtures + export roundtrip extension

**Files:**
- Modify: `server/tests/fixtures/records.py` (one helper), `server/tests/test_export_roundtrip.py`

**Interfaces:**
- Consumes: capture-with-units (Task 3).
- Produces: `make_pretty(line: bytes) -> bytes` — re-serializes any records.py builder line to 2-space-indented pretty bytes, newline-terminated. Task 5's fixtures reuse it.

- [ ] **Step 1: Add the helper** (records.py, beside `_encode`, with the module's no-real-text rule respected):

```python
def make_pretty(line: bytes) -> bytes:
    """Re-serialize a compact builder line as pretty-printed multi-line bytes (spec §2's
    hand-edit shape): 2-space indent, one field per line, newline-terminated."""
    return (json.dumps(json.loads(line), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
```

- [ ] **Step 2: Write the failing roundtrip test** (`test_export_roundtrip.py`, beside `test_roundtrip_no_trailing_newline`, same shape):

```python
def test_roundtrip_pretty_printed_head_byte_identical(db_session, tmp_path):
    """Compat spec §2's export covenant: a hand-pretty-printed head exports exactly
    as it sits on disk — reassembly moves boundaries, never bytes."""
    root = tmp_path / "r"
    slug = root / "-Users-x-pretty"
    slug.mkdir(parents=True)
    uuid = "6b6b6b6b-0000-4000-8000-000000000006"
    content = (
        make_pretty(make_user_line(text="edited by hand", sessionId=uuid))
        + make_pretty(make_thin_meta_line("mode", session_id=uuid))
        + make_assistant_line(text="native tail", sessionId=uuid)
    )
    (slug / f"{uuid}.jsonl").write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    assert export_transcript(db_session, uuid) == content
```

- [ ] **Step 3: Run — expect PASS immediately if Task 3 is correct** (this is a covenant test, not a feature test — if it fails, Task 3 has a byte bug and THIS task stops and reports rather than patching around it). Also run the full existing roundtrip file: `uv run pytest tests/test_export_roundtrip.py -q`
- [ ] **Step 4: Full suite, ruff, commit**

```bash
git add server/tests/fixtures/records.py server/tests/test_export_roundtrip.py
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: pretty-printed roundtrip covenant test + make_pretty fixture helper (compat spec §6)"
```

---

### Task 5: `introspect recapture`

**Files:**
- Create: `server/src/introspect/ingest/recapture.py`
- Modify: `server/src/introspect/cli.py` (subcommand), `server/src/introspect/models.py:121-132` (ImportRun.trigger comment only)
- Test: `server/tests/test_recapture.py` (new)

**Interfaces:**
- Consumes: `read_complete_units` (Task 2); reparse's discipline (`_INTERPRETATION_ANOMALY_KINDS` — import it from `reparse.py`, do not duplicate); `interpret.apply` + savepoint idiom (reparse.py:129-168); FTS `delete-all` is too broad here — de-index per-file like the demotion path (`interpret.remove_interpretation_for_source_file`'s de-index-then-delete order, interpret.py:139-142); ImportRun row idiom (run.py:181-203).
- Produces: `recapture_file(db, session_uuid, kind='main', agent_hex_id=None, dry_run=False) -> RecaptureStats` with `RecaptureStats(records_before, records_after, anomalies_before, anomalies_after, reconciled: bool)`; CLI `introspect recapture <session-uuid> [--kind main] [--agent-hex ...] [--db ...] [--dry-run]`.

- [ ] **Step 1: Write the failing tests.** Fixture: build a SHATTERED archive state the way the incident did — write a pretty file, capture it with the OLD splitting by monkeypatching capture to per-line units? No — simpler and honest: construct the shattered state through the REAL current pipeline by writing a file whose pretty region uses NON-opener first lines? Cleanest deterministic path: write the pretty file, capture it normally (Task 3 makes that clean), then simulate the legacy-shattered state directly in the DB is over-coupled. Instead: keep one REAL legacy path — capture the file with `read_complete_lines` semantics via a tiny test-only shim that calls `_capture_chunk` with single-line units (import the internals in the test; the repo's tests already reach into capture internals — see test_capture.py's use of `_capture_all`). Cases:

```python
def test_recapture_heals_shattered_pretty_file(db_session, tmp_path):
    # 1. Write pretty+compact file; capture it SHATTERED via the per-line shim.
    #    Assert the incident shape: N invalid_json anomalies, 0 messages from the head.
    # 2. recapture_file(db, uuid) with dry_run=True: stats show the would-be swap,
    #    DB unchanged (same anomaly count, same record ids).
    # 3. recapture_file(db, uuid): reconciled True; head records now reassembled=True;
    #    anomalies (invalid_json for this file) == 0; messages/blocks exist; FTS finds
    #    a term from the pretty head (search_index query); export still byte-identical.
    ...

def test_recapture_refuses_on_byte_mismatch(db_session, tmp_path):
    # Capture file normally; then corrupt ONE stored raw_line in the DB (simulate drift);
    # recapture must refuse: reconciled False, non-zero-style outcome, NOTHING mutated
    # (record ids, anomaly counts, checkpoints all unchanged).
    ...

def test_recapture_is_idempotent(db_session, tmp_path):
    # Healthy reassembled file → recapture is a reconciled no-op: same record count,
    # same shas, anomaly count unchanged; run twice.
    ...

def test_recapture_preserves_capture_phase_bookkeeping(db_session, tmp_path):
    # Force a source_diverged anomaly (rewrite line 1 + re-capture, the
    # test_export_roundtrip.py:72-79 recipe), then recapture the new generation's file:
    # the source_diverged row must survive.
    ...

def test_recapture_records_run_row(db_session, tmp_path):
    # After a heal: newest ImportRun has trigger='recapture', status 'ok',
    # records_added == records_after, anomaly_count reflecting the file's new state.
    ...
```

Write all five in full; the comments are their contracts. The per-line shim (test-local, ~8 lines) reuses `read_complete_lines` + the pre-Task-3 loop shape.

- [ ] **Step 2: Run — expect FAIL** (module not found): `uv run pytest tests/test_recapture.py -q`
- [ ] **Step 3: Implement `recapture.py`.** Structure (mirroring reparse's file layout and docstring voice):

  1. Resolve the SourceFile exactly as export does (`export._resolve_source_file` — import it; primary-or-most-complete is the same "which file is this session" question).
  2. Re-split: `new_units = list(read_complete_units(path, from_offset=0))`, truncated to units whose `end_offset <= source_file.byte_offset_checkpoint` (bytes beyond the checkpoint belong to a future `import`, not to recapture); if a unit STRADDLES the checkpoint, refuse (diagnosis: "reassembly crosses the capture checkpoint — run introspect import first").
  3. **The gate:** `b"".join(u.data for u in new_units) == b"".join(stored raw_line ORDER BY line_number)`. Mismatch → `RecaptureStats(..., reconciled=False)`, zero mutation. `dry_run` stops here either way, reporting would-be counts.
  4. The swap, one transaction: de-index the file's blocks from FTS (demotion-path order); delete the file's ContentBlocks/TokenUsage/Messages/SessionEvents (by join through raw_records.source_file_id — reparse deletes globally, recapture must scope per-file); delete the file's interpretation-class anomalies (`kind.in_(_INTERPRETATION_ANOMALY_KINDS) & source_file_id == file.id` — never the bookkeeping kinds); reset the affected session's cached title/bounds columns (scoped version of reparse's `_reset_session_caches`); delete the file's RawRecords; re-insert via the Task-3 `_capture_chunk` path with `bypass_dedup=True` (the divergence-regeneration precedent) — checkpoint/prefix_hash/last_size are NOT touched (same bytes by the gate); re-interpret each with the reparse savepoint idiom.
  5. ImportRun row: `trigger="recapture"`, started/finished, `records_added=records_after`, anomaly_count = this run's new anomalies (baseline-id pattern from run.py:181). Update the `trigger` comment in models.py to `'cli' | 'api' | 'recapture'`.
  6. CLI: subcommand per the `_cmd_reparse` idiom — same lock (`import.lock`), same exit codes (2 config, 1 refuse/failure with the diagnosis on stderr, 0 success), summary line `recapture file=<path> reconciled=<bool> records <before>-><after> anomalies <before>-><after>`.

- [ ] **Step 4: Run — expect PASS**, full suite, ruff.
- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/ingest/recapture.py server/src/introspect/cli.py server/src/introspect/models.py server/tests/test_recapture.py
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: introspect recapture — byte-reconciled healing of shattered files (compat spec §3)"
```

---

### Task 6: `introspect-schema/5`

**Files:**
- Modify: `server/src/introspect/schema/v1.py` (fields + DIFF_NOTES + SCHEMA_VERSION), `server/tests/test_schema_v1.py` (version pin + field pins), `server/tests/test_schema_versions.py` (known-count pin)
- Test: same files

**Interfaces:**
- Consumes: the schema/4 idiom exactly (fields with NOTE comments, DIFF_NOTES entry, opaque-`Any` rule for container payloads).
- Produces: `SCHEMA_VERSION == "introspect-schema/5"`; reparse (existing) clears the 24 `unknown_field` anomalies in production.

Field placements (census-verified against live anomaly details — the implementer re-verifies each against 2-3 live detail payloads via read-only queries if the archive is reachable, else trusts this table):

| Field | Record | Type |
|---|---|---|
| `interruptedByShutdown` | UserRecord | `bool \| None` |
| `source` | UserRecord | `str \| None` |
| `userFeedback` | UserRecord | `Any \| None` (opaque) |
| `isAbortedMidStream` | AssistantRecord | `bool \| None` |
| `pendingWorkflowCount` | AssistantRecord | `int \| None` |
| `logicalParentUuid` | UserRecord | `str \| None` |
| `compactMetadata` | UserRecord | `Any \| None` (opaque) |
| `isVisibleInTranscriptOnly` | UserRecord | `bool \| None` |
| `isCompactSummary` | UserRecord | `bool \| None` |

- [ ] **Step 1: Failing tests.** Update `test_schema_version_constant` to `"introspect-schema/5"`; add per-field pins in the `test_assistant_effort_and_api_error_fields_parse_ok` idiom (one test for the UserRecord family via `make_user_line(extra={...all six user fields...})`, one for AssistantRecord via `make_assistant_line(extra={...})`), asserting `status == "ok"` and `anomalies == []`; update `test_schema_versions.py`'s `schema_versions_known == 4` pin to `5` with a comment (`/5 recorded at first import after the bump — same shape as /4's arrival`).
- [ ] **Step 2: Run — expect FAIL** (constant still /4; extras produce `unknown_field`): `uv run pytest tests/test_schema_v1.py tests/test_schema_versions.py -q`
- [ ] **Step 3: Implement.** Declare the nine fields at their placements with a NOTE comment per family citing CLI 2.1.219/2.1.220 (third production-drift pass); DIFF_NOTES entry:

```python
    "introspect-schema/5": (
        "Third production-drift pass (CLI ~2.1.219-2.1.220). Declared: interruptedByShutdown, "
        "source, userFeedback (opaque), logicalParentUuid, compactMetadata (opaque), "
        "isVisibleInTranscriptOnly and isCompactSummary on UserRecord; isAbortedMidStream and "
        "pendingWorkflowCount on AssistantRecord. Census-driven; expected to drive the "
        "unknown_field floor from 24 to ~0. (2026-08-05 anomaly census)"
    ),
```

Bump `SCHEMA_VERSION`. No REGISTRY changes (no new record types).
- [ ] **Step 4: Run — expect PASS**, full suite, ruff.
- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/schema/v1.py server/tests/test_schema_v1.py server/tests/test_schema_versions.py
git commit --author="Claude (Haiku 4.5) <noreply@anthropic.com>" -m "server: introspect-schema/5 — nine census-verified drift fields declared (compat spec §4)"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/user/how-the-archive-protects-you.md`, `docs/user/export.md`, `docs/user/reading-room.md` (§resume), `docs/dev/README.md` (beside the reparse workflow)

- [ ] **Step 1:** Per spec §7, matching each file's voice: hand-edited-transcripts note (tolerated at capture, healed by `recapture`, exported byte-identically); export doc line (pretty sessions export exactly as stored); resume doc sentence (hand-edited sessions restore byte-exact and may not be consumable by `claude --resume` — a known, accepted property); dev README: `recapture` beside reparse with the byte-reconciliation gate called out and the reparse-vs-recapture division (interpretation vs record boundaries) in two sentences.
- [ ] **Step 2:** Read the diff against spec §5/§7 (no invented behavior, no missing note), commit:

```bash
git add docs/user/how-the-archive-protects-you.md docs/user/export.md docs/user/reading-room.md docs/dev/README.md
git commit --author="Claude (Haiku 4.5) <noreply@anthropic.com>" -m "docs: hand-edited transcript tolerance, recapture, schema/5 (compat spec §7)"
```

---

### Task 8: Heal production (orchestrator)

**Files:** none — operational, run by the orchestrating session with the owner's room live.

- [ ] **Step 1:** Full suites + ruff one final time.
- [ ] **Step 2:** `introspect recapture 8bda3f89-a369-47ab-8679-851969e1b815 --dry-run` against the production archive: verify reconciliation and the would-be counts (expect ~30,131 anomalies to fall). If reconciliation REFUSES, stop and report — do not force.
- [ ] **Step 3:** Run the real recapture, then `introspect reparse` (clears the 24 unknown_field under schema/5, restamps versions). Both respect `import.lock` — if the cron import is mid-run, wait for the lock.
- [ ] **Step 4:** Verify: `GET /api/v1/status` anomalies == 5 (3 `source_diverged` + 2 `source_reappeared`); the SBIR session's messages render in the reading room; its export remains byte-identical to the source file (spot-check via `cmp <(curl -s .../export.jsonl) <source path>`); the walk-era import history shows the `recapture` run row.
- [ ] **Step 5:** Report before/after to the owner with the census line: 30,160 → 5.

---

## Self-review (performed at write time)

- **Spec coverage:** §2→T1+T2+T3(+T4 covenant), §3→T5, §4→T6, §5 honored throughout (no source writes anywhere; no resume changes; opener-heuristic bounds tolerance), §6→each task's tests + T4, §7→T7; production heal→T8.
- **Placeholders:** T3 Step 2 and T5 Step 1 contain contract-comment test skeletons with explicit instructions to write them in full — the contracts are complete; T2's implementation excerpt is labeled architecture-not-gospel with its one stray `...` called out for deletion.
- **Type consistency:** `RawUnit` fields match between T2's definition and T3's consumption; `RecaptureStats` named consistently in T5; `reassembled` column name identical in T1/T3/T5; `make_pretty` signature identical in T4 definition and T5 usage.
- **Judgment calls recorded:** reassembly lives in reader.py (boundary logic beside boundary logic, capture stays record-agnostic); the reassembled marker is a real column per the ratified spec (explicit beats inferred-from-bytes); recapture scopes reparse's global deletes per-file rather than reusing them (reparse's `delete_all` FTS wipe would be collateral damage); EOF-open-buffer deferral mirrors the existing torn-write covenant at unit granularity — a plan-level refinement of spec §2's give-up rule, consistent with capture's live-append reality.
