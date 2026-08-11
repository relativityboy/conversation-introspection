# Message Authorship Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every archived message carries an accurate, precise authorship classification (who really authored it), rendered as core-voice + qualifier labels in the reader, with a three-state view control.

**Architecture:** A pure, total, never-raising classifier (`schema/authorship.py`) assigns `authorship_kind` / `authorship_basis` / `authorship_detail` per record via a 16-rule first-match tree; an idempotent ingest post-pass stores them on `messages` (schema generation `introspect-schema/7`, backfilled by reparse); the API replaces `chat_only` with `view=chat|chat-harness|all` using NULL-tolerant predicates; the web reader maps kind → label/accent and the toggle becomes three-state, localStorage-sticky.

**Tech Stack:** Python 3.12 / SQLAlchemy / Alembic / FastAPI / pytest; React + TypeScript / Vitest.

**Spec:** `docs/superpowers/specs/2026-08-07-message-authorship-labels-design.md` — binding; §3.2 is the rule tree, §3.3 the label map.

## Global Constraints

- Never write "Donovan" in tracked files — "the owner" or `relativityboy` (repo memory rule).
- Commits: terse conventional messages, author byline names the executing tier — `git commit --author="Claude (<Tier> <N>) <noreply@anthropic.com>"` — no Co-Authored-By footers.
- Never stage anything under `claude_notes/`.
- Zero-legacy (pre-release law): delete `chat_only` / `chatOnly` / `ChatOnlyToggle` outright — no aliases, no deprecation shims.
- The schema package stays DB-free: `schema/authorship.py` must not import `introspect.db` or `introspect.models` (v1.py module-docstring tenet).
- The classifier is total and never raises; `unclassified` is the only failure mode.
- Production DB work is read-only until Task 7 (the gated migrate+reparse).
- Python tests: `cd server && uv run pytest`; web tests: `cd web && npx vitest run`.

---

### Task 1: Authorship classifier (pure core)

**Files:**
- Create: `server/src/introspect/schema/authorship.py`
- Test: `server/tests/test_authorship.py`

**Interfaces:**
- Consumes: `introspect.schema.v1` parsed records (`UserRecord`, `AssistantRecord`, `SystemRecord`, `AttachmentRecord`, `BaseRecord`), `ToolUseBlock`.
- Produces (later tasks rely on these exact names):
  - `@dataclass(frozen=True) ToolUseRef(name: str, skill: str | None)`
  - `@dataclass(frozen=True) AuthorshipContext(transcript_kind: str, tool_uses: dict[str, ToolUseRef])`
  - `@dataclass(frozen=True) Authorship(kind: str, basis: str, detail: str | None)`
  - `classify(record: BaseRecord | None, ctx: AuthorshipContext) -> Authorship`
  - Constants: `PROMPT_SOURCE_ERA = (2, 1, 168)`, `KNOWN_PROMPT_SOURCES`, `KNOWN_ORIGIN_KINDS`, `CHAT_KINDS` (frozenset of the 8 §5 chat-view kinds).

- [ ] **Step 1: Write the failing tests** — one fixture per spec §3.2 rule (and the §2 census clusters), parametrized:

```python
"""Rule-by-rule tests for the authorship classifier (spec §3.2/§3.3)."""
import pytest
from introspect.schema import parse_line
from introspect.schema.authorship import (
    Authorship, AuthorshipContext, ToolUseRef, classify, CHAT_KINDS,
)

MAIN = AuthorshipContext(transcript_kind="main", tool_uses={})
SIDE = AuthorshipContext(transcript_kind="subagent", tool_uses={})


def user(content, version="2.1.220", **fields):
    rec = {"type": "user", "uuid": "u1", "version": version,
           "message": {"role": "user", "content": content}}
    rec.update(fields)
    return rec


def parsed(d):
    import json
    return parse_line(json.dumps(d)).record


# (fixture_record, ctx, expected_kind, expected_detail) — order mirrors spec §3.2
CASES = [
    # 1 compaction
    (user("summary", isCompactSummary=True, promptSource="typed"), MAIN, "compact_summary", None),
    # 2 tool result — detail from ctx map, echo alone never occurs but block is authoritative
    (user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
     AuthorshipContext("main", {"t1": ToolUseRef("Bash", None)}), "tool_result", "Bash"),
    # 3 skill injection (this very feature's brainstorm record shape)
    (user([{"type": "text", "text": "Base directory for this skill: ..."}],
          isMeta=True, sourceToolUseID="t2"),
     AuthorshipContext("main", {"t2": ToolUseRef("Skill", "superpowers:brainstorming")}),
     "skill_injection", "superpowers:brainstorming"),
    # 4 tool injection incl. dangling ref -> null detail
    (user([{"type": "text", "text": "# Chrome automation"}], isMeta=True, sourceToolUseID="gone"),
     MAIN, "tool_injection", None),
    # 5 task notification (both signal forms)
    (user("Agent finished", origin={"kind": "task-notification"}), MAIN, "task_notification", None),
    (user("Agent finished", promptSource="system"), MAIN, "task_notification", None),
    # 6 coordinator
    (user("The coordinator sent a message", isMeta=True, origin={"kind": "coordinator"}),
     SIDE, "coordinator", None),
    # 7 human typed / queued
    (user("hello", promptSource="typed", origin={"kind": "human"}), MAIN, "human_typed", None),
    (user("also this", promptSource="queued", origin={"kind": "human"}), MAIN, "human_queued", None),
    # 7 era-boundary cluster: typed with NO origin (CLI <= 2.1.177)
    (user("wake up!", version="2.1.170", promptSource="typed"), MAIN, "human_typed", None),
    # 8 sdk automation
    (user("Review this change for security issues", promptSource="sdk"), MAIN, "sdk_automation", None),
    # 9/10 command furniture
    (user("<command-name>/model</command-name>..."), MAIN, "command_expansion", "/model"),
    (user("<local-command-stdout>Login successful</local-command-stdout>"), MAIN, "command_output", None),
    # 11 harness meta: reminder / caveat / non-string document payload
    (user("<system-reminder>Respond tersely</system-reminder>", isMeta=True), MAIN, "harness_meta", "reminder"),
    (user("<local-command-caveat>Caveat: ...</local-command-caveat>", isMeta=True), MAIN, "harness_meta", "caveat"),
    (user([{"type": "document", "source": {"data": "JVBERi0="}}], isMeta=True), MAIN, "harness_meta", None),
    # 12 interrupt markers: string + array forms, tool variant, SIDECHAIN form, pre-era form
    (user("[Request interrupted by user]"), MAIN, "interrupt_marker", None),
    (user([{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
     MAIN, "interrupt_marker", "tool"),
    (user("[Request interrupted by user]", isSidechain=True), SIDE, "interrupt_marker", None),
    (user("[Request interrupted by user for tool use]", version="2.1.121"), MAIN, "interrupt_marker", "tool"),
    # 13 dispatch: sidechain opener; belt-and-suspenders (flag OR transcript kind)
    (user("You are implementing Task 3 of ...", isSidechain=True), SIDE, "dispatch", None),
    (user("You are implementing Task 3 of ..."), SIDE, "dispatch", None),  # flag lost, kind holds
    # 14 drift guard: unrecognized enum values -> unclassified, detail = the value
    (user("hi", promptSource="telepathy"), MAIN, "unclassified", "telepathy"),
    (user("hi", origin={"kind": "ouija"}), MAIN, "unclassified", "ouija"),
    # 15 pre-era inferred human (positive, version-gated)
    (user("Greetings Claude! Take your time reading", version="2.1.121"), MAIN, "human_inferred", None),
    # 16 floor: post-era fieldless MUST NOT be YOU; ancient no-version also floors
    (user("mystery text", version="2.1.220"), MAIN, "unclassified", None),
    (user("mystery text", version=None), MAIN, "unclassified", None),
]


@pytest.mark.parametrize("record,ctx,kind,detail", CASES)
def test_rule(record, ctx, kind, detail):
    got = classify(parsed(record), ctx)
    assert (got.kind, got.detail) == (kind, detail)


def test_non_user_types_are_total():
    a = parsed({"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": []}})
    s = parsed({"type": "system", "uuid": "s1", "subtype": "turn_duration", "durationMs": 5})
    att_h = parsed({"type": "attachment", "uuid": "at1", "attachment": {
        "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "hi"}})
    att_f = parsed({"type": "attachment", "uuid": "at2", "attachment": {"type": "skill_listing"}})
    assert classify(a, MAIN).kind == "claude"
    assert classify(s, MAIN) == Authorship("system", "verified — record type system", "turn_duration")
    assert classify(att_h, MAIN).kind == "attachment_queued_human"
    assert classify(att_f, MAIN).kind == "attachment_furniture"
    assert classify(None, MAIN).kind == "unclassified"  # unparseable line: total, never raises


def test_you_only_via_rules_7_and_15():
    """Spec §3.2 property: no harness-shaped fixture may classify human_*."""
    for record, ctx, kind, _ in CASES:
        if kind.startswith("human_"):
            continue
        assert not classify(parsed(record), ctx).kind.startswith("human_")


def test_basis_is_verified_or_heuristic_with_reason():
    got = classify(parsed(user("hello", promptSource="typed")), MAIN)
    assert got.basis.startswith("verified — ")
    got = classify(parsed(user("Greetings", version="2.1.121")), MAIN)
    assert got.basis.startswith("heuristic — ")


def test_chat_kinds_constant_matches_spec_section_5():
    assert CHAT_KINDS == frozenset({
        "human_typed", "human_queued", "human_inferred", "claude",
        "attachment_queued_human", "interrupt_marker", "dispatch", "coordinator"})
```

- [ ] **Step 2: Run to verify failure** — `cd server && uv run pytest tests/test_authorship.py -q` → FAIL (`ModuleNotFoundError: introspect.schema.authorship`).

- [ ] **Step 3: Implement the classifier** — the complete module; the rule order is the spec, comments cite rule numbers:

```python
"""Authorship classification (spec 2026-08-07 §3): who really authored each record.

Pure and DB-free by the same binding tenet as v1.py — all context (transcript kind,
tool_use map) arrives injected via AuthorshipContext. Total over every record type and
None; never raises. First-match rule order IS the contract; renumbering requires a spec
change.
"""
from __future__ import annotations

from dataclasses import dataclass

from introspect.schema.v1 import (
    AssistantRecord, AttachmentRecord, BaseRecord, SystemRecord, TextBlock,
    ToolResultBlock, UserRecord,
)

PROMPT_SOURCE_ERA = (2, 1, 168)  # first CLI version that stamps promptSource (verified 2026-08-07)
KNOWN_PROMPT_SOURCES = frozenset({"typed", "queued", "system", "sdk"})
KNOWN_ORIGIN_KINDS = frozenset({"human", "task-notification", "coordinator"})
INTERRUPT_PREFIX = "[Request interrupted by user"
CHAT_KINDS = frozenset({
    "human_typed", "human_queued", "human_inferred", "claude",
    "attachment_queued_human", "interrupt_marker", "dispatch", "coordinator",
})


@dataclass(frozen=True)
class ToolUseRef:
    name: str
    skill: str | None  # input["skill"] when name == "Skill", else None


@dataclass(frozen=True)
class AuthorshipContext:
    transcript_kind: str  # "main" | "subagent"
    tool_uses: dict[str, ToolUseRef]  # every tool_use in the record's transcript


@dataclass(frozen=True)
class Authorship:
    kind: str
    basis: str
    detail: str | None


def _version_tuple(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _first_text(record: UserRecord) -> str | None:
    content = record.message.content
    if isinstance(content, str):
        return content
    for block in content:
        if isinstance(block, TextBlock):
            return block.text
    return None


def _has_text_or_image(record: UserRecord) -> bool:
    content = record.message.content
    if isinstance(content, str):
        return True
    return any(
        isinstance(b, TextBlock) or getattr(b, "type", None) == "image" for b in content
    )


def _tool_result_id(record: UserRecord) -> str | None:
    content = record.message.content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, ToolResultBlock):
                return block.tool_use_id
    return None


def _origin_kind(record: UserRecord) -> str | None:
    origin = record.origin
    if isinstance(origin, dict):
        kind = origin.get("kind")
        return kind if isinstance(kind, str) else None
    return None


def _command_name(text: str) -> str | None:
    start = text.find("<command-name>")
    if start < 0:
        return None
    end = text.find("</command-name>", start)
    return text[start + len("<command-name>"): end].strip() if end > start else None


def _classify_user(record: UserRecord, ctx: AuthorshipContext) -> Authorship:
    text = _first_text(record)
    origin_kind = _origin_kind(record)
    ps = record.promptSource

    # 1 — compaction summary
    if record.isCompactSummary:
        return Authorship("compact_summary", "verified — isCompactSummary", None)
    # 2 — tool result (block authoritative; toolUseResult echo never occurs alone)
    tr_id = _tool_result_id(record)
    if tr_id is not None or record.toolUseResult is not None:
        ref = ctx.tool_uses.get(tr_id) if tr_id else None
        return Authorship("tool_result", "verified — tool_result block", ref.name if ref else None)
    # 3/4 — injected content, kind decided by the referenced tool
    if record.sourceToolUseID is not None:
        ref = ctx.tool_uses.get(record.sourceToolUseID)
        if ref is not None and ref.name == "Skill":
            return Authorship("skill_injection", "verified — sourceToolUseID → Skill", ref.skill)
        return Authorship(
            "tool_injection", "verified — sourceToolUseID", ref.name if ref else None)
    # 5 — task notification (either signal)
    if origin_kind == "task-notification" or ps == "system":
        return Authorship("task_notification", "verified — origin/promptSource", None)
    # 6 — coordinator relay
    if origin_kind == "coordinator":
        return Authorship("coordinator", "verified — origin.kind coordinator", None)
    # 7 — the human, explicitly stamped
    if ps == "typed":
        return Authorship("human_typed", "verified — promptSource: typed", None)
    if ps == "queued":
        return Authorship("human_queued", "verified — promptSource: queued", None)
    # 8 — programmatic callers
    if ps == "sdk":
        return Authorship("sdk_automation", "verified — promptSource: sdk", None)
    # 9/10 — slash-command furniture
    if isinstance(text, str) and text.startswith("<command-name>"):
        return Authorship("command_expansion", "heuristic — <command-name> prefix", _command_name(text))
    if isinstance(text, str) and text.startswith("<local-command-stdout>"):
        return Authorship("command_output", "heuristic — <local-command-stdout> prefix", None)
    # 11 — remaining CLI-internal records (also the home for non-string meta payloads)
    if record.isMeta:
        detail = None
        if isinstance(text, str):
            if text.startswith("<system-reminder>"):
                detail = "reminder"
            elif text.startswith("<local-command-caveat>"):
                detail = "caveat"
        return Authorship("harness_meta", "heuristic — isMeta", detail)
    # 12 — interruption markers, BEFORE the sidechain rule (spec §2: 4 sidechain instances)
    if isinstance(text, str) and text.startswith(INTERRUPT_PREFIX):
        detail = "tool" if text.startswith(INTERRUPT_PREFIX + " for tool use") else None
        return Authorship("interrupt_marker", "heuristic — interrupt marker text", detail)
    # 13 — dispatcher-authored records in subagent transcripts (flag OR transcript kind:
    # a hand-edited record that lost isSidechain must not cross to human — spec §3.2)
    if record.isSidechain or ctx.transcript_kind == "subagent":
        return Authorship("dispatch", "verified — sidechain transcript", None)
    # 14 — drift guard: recognizable fields carrying unrecognized values -> alarm, never YOU
    if ps is not None and ps not in KNOWN_PROMPT_SOURCES:
        return Authorship("unclassified", "verified — unrecognized promptSource", ps)
    if origin_kind is not None and origin_kind not in KNOWN_ORIGIN_KINDS:
        return Authorship("unclassified", "verified — unrecognized origin.kind", origin_kind)
    # 15 — pre-promptSource-era inference (POSITIVE, version-gated: from 2.1.168 every human
    # prompt is stamped, so a post-era fieldless record is evidence of harness origin)
    version = _version_tuple(record.version)
    if (
        ctx.transcript_kind == "main"
        and not record.isSidechain
        and version is not None
        and version < PROMPT_SOURCE_ERA
        and _has_text_or_image(record)
    ):
        return Authorship(
            "human_inferred",
            "heuristic — pre-2.1.168 record, main transcript, no harness markers", None)
    # 16 — the honest floor / drift alarm
    return Authorship("unclassified", "heuristic — no rule matched", None)


def classify(record: BaseRecord | None, ctx: AuthorshipContext) -> Authorship:
    if record is None:
        return Authorship("unclassified", "heuristic — unparseable record", None)
    if isinstance(record, UserRecord):
        return _classify_user(record, ctx)
    if isinstance(record, AssistantRecord):
        return Authorship("claude", "verified — record type assistant", None)
    if isinstance(record, SystemRecord):
        return Authorship("system", "verified — record type system", record.subtype)
    if isinstance(record, AttachmentRecord):
        if record.blocks():
            return Authorship(
                "attachment_queued_human", "verified — rescued human queued_command", None)
        return Authorship("attachment_furniture", "verified — zero-block attachment", None)
    return Authorship("unclassified", "heuristic — unregistered record type", None)
```

- [ ] **Step 4: Run to verify pass** — `cd server && uv run pytest tests/test_authorship.py -q` → all PASS.
- [ ] **Step 5: Commit** — `git add server/src/introspect/schema/authorship.py server/tests/test_authorship.py && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: authorship classifier — 16-rule tree, total, DB-free (spec §3)"`

---

### Task 2: Columns, index, and API model fields

**Files:**
- Create: `server/alembic/versions/0008_authorship_columns.py`
- Modify: `server/src/introspect/models.py` (class `Message`, after `request_id`)
- Modify: `server/src/introspect/api/models.py` (class `MessageOut`, after `blocks`)
- Modify: `web/src/api/types.ts` (interface `MessageOut`)
- Test: `server/tests/test_migrations.py` (append; follow the existing upgrade-test pattern in that file — if it doesn't exist, create with the pattern below)

**Interfaces:**
- Produces: `Message.authorship_kind/basis/detail: Mapped[str | None]`; `MessageOut` (server + TS) carries the same three nullable strings; index `ix_messages_transcript_id (transcript_id, id)`.

- [ ] **Step 1: Failing test**

```python
def test_authorship_columns_and_transcript_index(tmp_path):
    from introspect.db import open_db  # session factory used across existing tests
    engine = open_db(tmp_path / "t.db")  # open_db runs alembic upgrade head
    import sqlalchemy as sa
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("messages")}
    assert {"authorship_kind", "authorship_basis", "authorship_detail"} <= cols
    assert any(ix["name"] == "ix_messages_transcript_id"
               and ix["column_names"] == ["transcript_id", "id"]
               for ix in insp.get_indexes("messages"))
```

(Adapt the `open_db` call to the project's actual test-DB helper — `server/tests/` already opens migrated temp DBs; copy that idiom exactly.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest server/tests/test_migrations.py -q` → FAIL (missing columns).
- [ ] **Step 3: Implement** — migration `0008` (`down_revision = "0007"`):

```python
"""authorship columns + transcript index (spec §4)"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.add_column("messages", sa.Column("authorship_kind", sa.String(), nullable=True))
    op.add_column("messages", sa.Column("authorship_basis", sa.String(), nullable=True))
    op.add_column("messages", sa.Column("authorship_detail", sa.String(), nullable=True))
    op.create_index("ix_messages_transcript_id", "messages", ["transcript_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_messages_transcript_id", table_name="messages")
    op.drop_column("messages", "authorship_detail")
    op.drop_column("messages", "authorship_basis")
    op.drop_column("messages", "authorship_kind")
```

`models.py` `Message` gains (after `request_id`):

```python
    authorship_kind: Mapped[str | None]
    authorship_basis: Mapped[str | None]
    authorship_detail: Mapped[str | None]
```

`api/models.py` `MessageOut` gains (after `blocks`; `from_attributes` maps them):

```python
    authorship_kind: str | None
    authorship_basis: str | None
    authorship_detail: str | None
```

`web/src/api/types.ts` `MessageOut` gains:

```typescript
  authorship_kind: string | null
  authorship_basis: string | null
  authorship_detail: string | null
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest server/tests/test_migrations.py -q` → PASS; full suite still green: `uv run pytest -q`.
- [ ] **Step 5: Commit** — `git add server/alembic/versions/0008_authorship_columns.py server/src/introspect/models.py server/src/introspect/api/models.py web/src/api/types.ts && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server+web: authorship columns, (transcript_id,id) index, MessageOut fields"`

---

### Task 3: Ingest post-pass + schema/7 bump

**Files:**
- Modify: `server/src/introspect/ingest/interpret.py` (append the post-pass functions)
- Modify: `server/src/introspect/ingest/run.py` (call after interpretation completes)
- Modify: `server/src/introspect/ingest/reparse.py` (call after interpretation completes; print census)
- Modify: `server/src/introspect/schema/v1.py` (`SCHEMA_VERSION` → `introspect-schema/7`; add `DIFF_NOTES` entry)
- Test: `server/tests/test_authorship_apply.py`

**Interfaces:**
- Consumes: `classify`, `AuthorshipContext`, `ToolUseRef` from Task 1; `parse_line` from the schema package; `Message`, `ContentBlock`, `RawRecord`, `Transcript` ORM.
- Produces: `classify_pending(db: Session) -> collections.Counter` in `interpret.py` — classifies every `messages` row where `authorship_kind IS NULL`, returns count-by-kind. Idempotent; incremental imports only touch new rows; reparse (which clears interpretation) re-derives everything.

- [ ] **Step 1: Failing test** — build a migrated temp DB with the project's existing ingest-fixture idiom (see `server/tests/` for the import-a-fixture-file pattern; copy it), containing one main transcript with: a typed human record, a tool_result record whose tool_use appears AFTER it in file order (the out-of-order production case), a Skill-injection record, and one subagent transcript with a dispatch opener. Assert after `classify_pending`:

```python
def test_classify_pending_populates_and_is_idempotent(ingested_db):
    from introspect.ingest.interpret import classify_pending
    census = classify_pending(ingested_db)
    assert census["human_typed"] == 1
    assert census["tool_result"] == 1
    assert census["skill_injection"] == 1
    assert census["dispatch"] == 1
    row = ingested_db.execute(sa.text(
        "SELECT authorship_detail FROM messages WHERE authorship_kind='tool_result'"
    )).scalar_one()
    assert row == "Bash"  # resolved DESPITE the tool_use appearing later in file order
    assert classify_pending(ingested_db).total() == 0  # idempotent: nothing left NULL
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest server/tests/test_authorship_apply.py -q` → FAIL (`classify_pending` undefined).
- [ ] **Step 3: Implement** — append to `interpret.py`:

```python
def _transcript_context(db: Session, transcript_id: int) -> "AuthorshipContext":
    """Whole-transcript tool_use map, built in memory BEFORE classifying any record —
    this is what makes rules 2-4 order-independent (18 production tool_results precede
    their tool_use in file order) and O(1) per record instead of a 54k-row scan."""
    from introspect.schema.authorship import AuthorshipContext, ToolUseRef

    rows = db.execute(
        select(ContentBlock.tool_use_id, ContentBlock.tool_name, ContentBlock.payload)
        .join(Message, ContentBlock.message_id == Message.id)
        .where(Message.transcript_id == transcript_id,
               ContentBlock.block_kind == "tool_use",
               ContentBlock.tool_use_id.is_not(None))
    ).all()
    tool_uses = {}
    for tool_use_id, tool_name, payload in rows:
        skill = None
        if tool_name == "Skill" and isinstance(payload, dict):
            value = payload.get("skill")
            skill = value if isinstance(value, str) else None
        tool_uses[tool_use_id] = ToolUseRef(name=tool_name or "", skill=skill)
    kind = db.scalar(select(Transcript.kind).where(Transcript.id == transcript_id))
    return AuthorshipContext(transcript_kind=kind or "main", tool_uses=tool_uses)


def classify_pending(db: Session) -> Counter:
    """Classify every message row with NULL authorship_kind. Idempotent post-pass called
    by both import and reparse after interpretation; returns the census by kind."""
    from introspect.schema import parse_line
    from introspect.schema.authorship import classify

    census: Counter = Counter()
    pending_transcripts = db.scalars(
        select(Message.transcript_id).where(Message.authorship_kind.is_(None)).distinct()
    ).all()
    for transcript_id in pending_transcripts:
        ctx = _transcript_context(db, transcript_id)
        rows = db.execute(
            select(Message.id, RawRecord.raw_line)
            .join(RawRecord, Message.raw_record_id == RawRecord.id)
            .where(Message.transcript_id == transcript_id,
                   Message.authorship_kind.is_(None))
        ).all()
        for message_id, raw_line in rows:
            text = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
            authorship = classify(parse_line(text).record, ctx)
            db.execute(
                update(Message).where(Message.id == message_id).values(
                    authorship_kind=authorship.kind,
                    authorship_basis=authorship.basis,
                    authorship_detail=authorship.detail,
                )
            )
            census[authorship.kind] += 1
    return census
```

(Import `Counter`, `select`, `update`, `Transcript`, `RawRecord` at module top alongside the existing imports.) Call sites — in `run.py` and `reparse.py`, after the interpretation loop finishes and before the final commit/summary, add:

```python
    census = classify_pending(db)
    # reparse.py additionally prints, next to its existing summary output:
    for kind, count in sorted(census.items(), key=lambda kv: -kv[1]):
        print(f"  authorship {kind}: {count}")
    if census.get("unclassified"):
        print(f"  !! unclassified: {census['unclassified']} — drift alarm (spec §7)")
```

`v1.py`: set `SCHEMA_VERSION = "introspect-schema/7"` and append to `DIFF_NOTES`:

```python
    "introspect-schema/7": (
        "Interpretation change only, no new declared fields (the /3 precedent): the "
        "authorship classifier (schema/authorship.py, spec 2026-08-07) populates "
        "authorship_kind/basis/detail on messages via the classify_pending ingest "
        "post-pass. 16-rule first-match tree; unclassified is the drift alarm."
    ),
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest server/tests/test_authorship_apply.py server/tests/test_schema_v1.py -q` → PASS (test_schema_v1 has version-string assertions — update the expected literal to `/7` where asserted). Full suite green.
- [ ] **Step 5: Commit** — `git add -u server && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: classify_pending post-pass + introspect-schema/7 — authorship backfilled at import/reparse"`

---

### Task 4: API — `view` parameter replaces `chat_only`

**Files:**
- Modify: `server/src/introspect/api/routes/sessions.py` (`_chat_only_filter`, `list_messages`)
- Test: `server/tests/` — extend the existing messages-endpoint test module (locate `list_messages`/`chat_only` tests by grep; extend in place)

**Interfaces:**
- Consumes: `CHAT_KINDS` from Task 1; populated `authorship_kind` from Task 3.
- Produces: `GET /transcripts/{id}/messages?view=chat|chat-harness|all` (default `all`); `chat_only` is GONE (zero-legacy). The filter builder: `_view_filter(view: str) -> ColumnElement`.

- [ ] **Step 1: Failing tests** — in the endpoint test module, replace every `chat_only=true` usage with `view=` equivalents and add:

```python
def test_view_chat_shows_dialogue_and_doors(client, seeded_transcript):
    # seeded: human_typed + claude-with-text + tool_result record + skill_injection
    #         + interrupt_marker + NULL-kind row (pre-reparse simulation)
    ids = lambda r: [m["record_uuid"] for m in r.json()["items"]]
    chat = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=chat")
    assert "u-human" in ids(chat) and "u-interrupt" in ids(chat)
    assert "u-toolresult" not in ids(chat) and "u-skill" not in ids(chat)
    assert "u-nullkind" in ids(chat)  # NULL falls back to legacy type+content rule
    harness = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=chat-harness")
    assert "u-skill" in ids(harness) and "u-toolresult" not in ids(harness)
    everything = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?view=all")
    assert "u-toolresult" in ids(everything)

def test_chat_only_param_is_gone(client, seeded_transcript):
    r = client.get(f"/api/v1/transcripts/{seeded_transcript}/messages?chat_only=true&view=all")
    assert [m["record_uuid"] for m in r.json()["items"]]  # unknown params ignored, view rules

def test_view_rejects_unknown_value(client, seeded_transcript):
    assert client.get(
        f"/api/v1/transcripts/{seeded_transcript}/messages?view=bogus").status_code == 422
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** — in `sessions.py`, delete `_chat_only_filter` and the `chat_only` parameter; add (the `has_content` EXISTS clause is MOVED verbatim from the old filter into `_prose_visible()`):

```python
_LEGACY_TYPES = ("user", "assistant", "attachment")
VIEW_VALUES = ("chat", "chat-harness", "all")


def _prose_visible() -> ColumnElement:
    # (the existing EXISTS-over-blocks clause from the old _chat_only_filter, unchanged)
    ...


def _view_filter(view: str) -> ColumnElement:
    """Spec §5. NULL-tolerant: rows not yet backfilled (migrate→reparse window) degrade
    to the legacy type+content rule, never to an empty reader."""
    if view == "all":
        return true()
    legacy_fallback = and_(Message.authorship_kind.is_(None),
                           Message.type.in_(_LEGACY_TYPES))
    if view == "chat":
        from introspect.schema.authorship import CHAT_KINDS
        kind_ok = or_(Message.authorship_kind.in_(sorted(CHAT_KINDS)), legacy_fallback)
    else:  # chat-harness
        kind_ok = or_(Message.authorship_kind.is_(None),
                      Message.authorship_kind != "tool_result")
        kind_ok = and_(or_(kind_ok, legacy_fallback), Message.type.in_(_LEGACY_TYPES))
    return and_(kind_ok, _prose_visible())
```

and in `list_messages`: `view: Literal["chat", "chat-harness", "all"] = "all"`, `type_filter = _view_filter(view)` (still built once, applied at all four query sites — preserve that discipline; the module docstring says why).

- [ ] **Step 4: Run to verify pass** — endpoint module + full server suite green.
- [ ] **Step 5: Commit** — `git add -u server && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "api: view=chat|chat-harness|all replaces chat_only; NULL-tolerant predicates (spec §5)"`

---

### Task 5: Web — three-state view mode (state, toggle, recovery)

**Files:**
- Create: `web/src/lib/viewMode.ts` (successor of `chatOnly.ts`)
- Delete: `web/src/lib/chatOnly.ts`
- Create: `web/src/components/reader/ViewToggle.tsx` (successor of `ChatOnlyToggle.tsx`; delete the old file)
- Modify: every importer of `useChatOnly` / `isChatOnlyVisible` / `ChatOnlyToggle` (grep: `SessionPage.tsx`, `ConversationView.tsx`, `MessageTurn.tsx`, `RawRecordInspector.tsx`, tests)
- Test: `web/src/lib/viewMode.test.ts` (successor of chatOnly tests)

**Interfaces:**
- Produces (exact):
  - `type ViewMode = 'chat' | 'chat-harness' | 'all'` (default **`'chat'`**)
  - `useViewMode(): { view: ViewMode; setView: (v: ViewMode) => void }` — one owner per reader page (same hook-ownership rule documented in the old chatOnly.ts header), localStorage key `introspect.view.v1`; the old `introspect.chatOnly.v1` key is ignored and REMOVED on first write (zero-legacy, no value migration).
  - `isVisibleInView(message: MessageOut, view: ViewMode): boolean` — client mirror of `_view_filter` incl. the NULL fallback (`authorship_kind === null` → legacy type check), used by both the reader and inspector prev/next so visibility can never drift.
  - `CHAT_KINDS: ReadonlySet<string>` — literal copy of the server set; a comment names `schema/authorship.py` as the source of truth.

- [ ] **Step 1: Failing tests** — port the existing chatOnly tests to three states; core cases:

```typescript
it('defaults to chat and persists via introspect.view.v1', ...)
it('removes the legacy introspect.chatOnly.v1 key on first write', ...)
it('isVisibleInView: interrupt_marker visible in chat; skill_injection only in chat-harness/all; tool_result only in all', ...)
it('isVisibleInView: null kind falls back to legacy type rule in every view', ...)
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/lib/viewMode.test.ts`.
- [ ] **Step 3: Implement** — `viewMode.ts` (carry over the storage-throw guards from chatOnly.ts verbatim); `ViewToggle.tsx` renders the three-segment control labeled `chat · chat+harness · all` styled like the current toggle; `ConversationView.tsx` around-404 recovery button becomes `setView('all')` with copy "show all message types". Update all importers; delete both old files.
- [ ] **Step 4: Run to verify pass** — `npx vitest run` fully green; `grep -rn "chatOnly\|ChatOnly" web/src` returns nothing.
- [ ] **Step 5: Commit** — `git add -A web/src && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: three-state view mode replaces chatOnly — sticky, NULL-tolerant, 404-recovery targets all"`

---

### Task 6: Web — authorship labels, per-view blocks, inspector line

**Files:**
- Modify: `web/src/components/reader/MessageTurn.tsx` (replace `voiceOf`/`SPEAKER`/`ACCENT`)
- Modify: `web/src/components/reader/RawRecordInspector.tsx` (classification line in the header area)
- Test: `web/src/components/reader/MessageTurn.test.tsx` (extend existing)

**Interfaces:**
- Consumes: `MessageOut.authorship_*` (Task 2), `ViewMode` (Task 5).
- Produces: `speakerFor(message: MessageOut): { label: string; accent: string }` exported from MessageTurn.tsx for tests.

- [ ] **Step 1: Failing tests** — every §3.3 row, plus block behavior:

```typescript
const cases: Array<[string, string | null, string]> = [
  ['human_typed', null, 'YOU'], ['human_queued', null, 'YOU'], ['human_inferred', null, 'YOU'],
  ['claude', null, 'CLAUDE'], ['dispatch', null, 'CLAUDE (DISPATCH)'],
  ['coordinator', null, 'CLAUDE (COORDINATOR)'],
  ['tool_result', 'Bash', 'SYSTEM (TOOL RESULT)'],
  ['skill_injection', 'superpowers:brainstorming', 'SYSTEM (SKILL: brainstorming)'], // prefix stripped
  ['tool_injection', 'ToolSearch', 'SYSTEM (INJECTED: toolsearch)'],
  ['tool_injection', null, 'SYSTEM (INJECTED)'],
  ['task_notification', null, 'SYSTEM (TASK NOTIFICATION)'],
  ['sdk_automation', null, 'SYSTEM (AUTOMATION)'],
  ['command_expansion', '/model', 'SYSTEM (COMMAND: /model)'],
  ['command_output', null, 'SYSTEM (COMMAND OUTPUT)'],
  ['harness_meta', 'reminder', 'SYSTEM (REMINDER)'], ['harness_meta', 'caveat', 'SYSTEM (CAVEAT)'],
  ['harness_meta', null, 'SYSTEM (META)'],
  ['interrupt_marker', null, 'SYSTEM (INTERRUPT)'], ['interrupt_marker', 'tool', 'SYSTEM (INTERRUPT)'],
  ['compact_summary', null, 'SYSTEM (COMPACTION)'],
  ['unclassified', null, 'SYSTEM (UNCLASSIFIED)'],
  ['system', 'turn_duration', 'SYSTEM (TURN DURATION)'], ['system', null, 'SYSTEM'],
  ['attachment_queued_human', null, 'SYSTEM (YOU)'], ['attachment_furniture', null, 'SYSTEM'],
]
// null-kind legacy fallback: renders the OLD type-derived labels (YOU/CLAUDE/SYSTEM)
// accent tests: dawn ONLY for human_* and attachment_queued_human; dragonfly for claude/dispatch/coordinator; mist otherwise
// block tests: in 'chat'/'chat-harness', tool_result blocks hidden; tool_use renders
//   ONLY when SubagentChip resolves a subagent dispatch; in 'all' everything renders
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** — `speakerFor` builds label from kind + detail (qualifier text lowercase; skill detail takes the substring after the last `:`; system subtype uppercased with `_`→space). Accents: `dawn` for `human_*`/`attachment_queued_human`, `dragonfly` for `claude`/`dispatch`/`coordinator`, `mist` otherwise; NULL kind falls back to the current `voiceOf` type logic (keep it as `legacyVoiceOf`, called only on null). Block dispatch: `chatOnly` prop becomes `view: ViewMode`; `tool_use` case renders `<SubagentChip>` in non-`all` views only when the transcripts-context join resolves (SubagentChip already exposes that resolution — give it a `renderFallback: boolean` prop: `false` in non-all views, `true`—current ToolBlock fallback—in `all`). Inspector: one mono line above the raw JSON: `authorship: {kind} ({basis}{detail ? ` — ${detail}` : ''})`. Preserve the existing NOTE(claude) comment about generic speaker names; extend it to name the §3.3 map as the one label source.
- [ ] **Step 4: Run to verify pass** — full web suite green.
- [ ] **Step 5: Commit** — `git add -u web/src && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: authorship labels + per-view blocks + inspector classification line (spec §3.3/§6)"`

---

### Task 7: Production migrate + reparse + census gate  ⚠️ first write to the production archive

**Files:** none created — operational task, run from `server/`.

- [ ] **Step 1:** Preconditions: full server+web suites green; `git status` clean except staged docs.
- [ ] **Step 2:** Stop the cron importer for the window (`crontab -l` to find it; comment the introspect line; restore after Step 5).
- [ ] **Step 2a — BACKUP (owner requirement, 2026-08-07; owner-specified form: just copy
  the file):** with cron stopped (no writers), run
  `mkdir -p ~/.conversation-introspection/backups && cp ~/.conversation-introspection/archive.db* ~/.conversation-introspection/backups/`
  — the `*` carries the `-wal`/`-shm` files along, which under WAL mode hold recent
  committed records until checkpoint; copying the family keeps the copy faithful. Record
  the backup paths in the SDD ledger. Retained until the owner prunes. If the copy fails,
  stop before migration. (Backlog, out of scope: the archive has no standing backup
  regimen.)
- [ ] **Step 3:** `uv run introspect status` (record baseline), then `uv run introspect reparse` (opens+migrates the DB first — the CLI's documented behavior — then rebuilds interpretation and prints the authorship census).
- [ ] **Step 4: The census gate (spec §8):** compare the printed census against the expected shape — `unclassified` MUST be 0; `human_typed` ≈ 420–450; `human_inferred` ≈ 55–70; `interrupt_marker` ≈ 35–45; `dispatch` > 400; `tool_result` > 18,000 (counts move with the live archive; magnitudes must hold). Sample-read 2 records from EVERY kind via `sqlite3 -readonly` and confirm the classification is honest. **If any cluster contradicts a rule: STOP, update the spec first, then the classifier, then re-run.** A nonzero `unclassified` is a finding to investigate, never to ship around.
- [ ] **Step 5:** Restore cron; run one `uv run introspect import` cycle; verify new records classify (no NULL kinds remain: `SELECT count(*) FROM messages WHERE authorship_kind IS NULL` → 0).
- [ ] **Step 6:** Append the census table to the SDD ledger note for the owner's review. No commit (nothing changed in the repo).

---

### Task 8: Docs + production walk

**Files:**
- Modify: `docs/user/reading-room.md` (view-modes section: what `chat · chat+harness · all` each show; the label vocabulary table from spec §3.3, condensed)
- Modify: `docs/dev/README.md` (schema/7 + classifier location + `classify_pending` note, following the existing schema-version workflow prose)
- Test: none (docs) + the manual walk below.

- [ ] **Step 1:** Write both doc updates. Copy label names exactly from spec §3.3.
- [ ] **Step 2: Production walk (spec §8)** — serve, then in the browser verify each: (a) this feature's own brainstorm session — the Skill expansion reads `SYSTEM (SKILL: brainstorming)`, not YOU; (b) a subagent transcript — opener reads `CLAUDE (DISPATCH)` and is VISIBLE in `chat` view; (c) a task notification renders `SYSTEM (TASK NOTIFICATION)`; (d) an interrupt marker is visible in `chat` as `SYSTEM (INTERRUPT)`; (e) a tool-dense stretch in `chat` view still shows subagent chips and hides ordinary tool blocks; (f) the inspector shows the classification line with basis; (g) the three-way toggle persists across reload; (h) a `tool_result` deeplink in `chat` view triggers the around-404 recovery to `all`. Screenshot each; file under the walk-image convention in repo root.
- [ ] **Step 3: Commit** — `git add docs/user/reading-room.md docs/dev/README.md walk-*.png && git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "docs: view modes + authorship label vocabulary; walk evidence"`

---

## Self-Review (performed at write time)

- **Spec coverage:** §3.1/§3.2 → T1; §3.3 → T6; §4 → T2+T3 (columns/index/tenet/map/ordering), T7 (deploy ordering executed); §5 → T4 (+T5 client mirror); §6 → T5+T6; §7 → T1 rule 16 + T3 census print + T7 gate; §8 → T1 tests, T7 gate, T8 walk; §9 non-goals untouched; §10.6/10.7 rulings encoded in T1 rule order, T4 kinds, T5 state model, T6 chip survival.
- **Placeholder scan:** `_prose_visible()` body is marked "moved verbatim from the old filter" — that is a move instruction, not a TBD; Task 2's `open_db` idiom and Task 3's fixture idiom explicitly direct copying the existing test patterns. No TBDs remain.
- **Type consistency:** `classify`/`classify_pending`/`CHAT_KINDS`/`ViewMode`/`useViewMode`/`isVisibleInView`/`speakerFor` names and signatures match across T1→T3→T4→T5→T6.
