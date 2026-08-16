# Search sources — chat-by-default, widen by flag (2026-08-15)

Ratified direction (relativityboy, in-session): the "mainly for Claude" read APIs should
trim search to *the chat* — the human and Claude talking to each other — by default, with
other sources addable explicitly. Wanted in the TUI as well. Motivation: retrieval
honesty. The archive's byte-faithful layer makes memory corruption impossible; the reading
layer is where noise creeps in. As recall-agents' own transcripts accumulate (rememberings
of rememberings), an unscoped search increasingly surfaces Claude-consulting-its-memory
instead of the conversation itself. Default to the source of truth; widen deliberately.

## §1 Census (real archive, read-only, 2026-08-15)

Indexed text blocks (the FTS predicate: `block_kind='text'`, non-empty), bucketed by
transcript kind × authorship_kind: 9,547 blocks / ~12.4 MB total.

- **chat** = main transcript ∧ authorship ∈ {human_typed, human_queued, human_inferred,
  attachment_queued_human, interrupt_marker, claude}: **2,549 blocks / ~1.5 MB** —
  27% of blocks, 12% of bytes.
- **agents** = subagent transcripts (their claude prose, dispatch briefings, coordinator
  relays, everything): 6,465 blocks / ~4.9 MB.
- **system** = main-transcript harness records (task_notification, sdk_automation,
  skill_injection, harness_meta, command_expansion/output, compact_summary…): 533 blocks
  / ~6.0 MB (sdk_automation and skill_injection carry huge blocks).
- unclassified in the index: **zero** (drift alarm quiet).
- The three buckets partition the index exactly: all dispatch/coordinator text lives in
  subagent transcripts, so `agents`+`system`+`chat` is total, no overlap.
- Tool payloads are not FTS-indexed at all → there is deliberately NO `tools` source
  (offering a no-op flag would be a silent lie).

## §2 The sources axis

`sources` = additive set over {`chat`, `agents`, `system`}; `all` = shorthand for the
full set. (Name chosen because `scope` is already taken by the search API's
global-vs-session axis.)

- **chat**: `t.kind='main' AND m.authorship_kind IN DIALOGUE_KINDS`. `DIALOGUE_KINDS`
  is a new frozenset in `schema/authorship.py` (the taxonomy's home): {human_typed,
  human_queued, human_inferred, attachment_queued_human, interrupt_marker, claude}.
  Deliberately stricter than the room's `CHAT_KINDS` (which includes dispatch +
  coordinator for chat-view rendering): interrupts are the human's voice (2026-08-08
  ruling: interrupts stay visible); dispatch briefings are Claude talking to minions,
  not to the human.
- **agents**: `t.kind='subagent'` (every authorship within).
- **system**: `t.kind='main' AND (authorship NOT IN DIALOGUE_KINDS OR authorship IS
  NULL)` — NULL (not-yet-classified mid-import) buckets as system, the honest floor:
  not known to be you-and-me.

## §3 Mechanism vs policy

- **Index layer** (`search/fts5.py`, the `SearchIndex` protocol): `search()`,
  `session_uuids_matching()`, `best_snippets()` gain `sources: frozenset[str] | None`;
  `None` = unfiltered (mechanism default unchanged — policy lives at the surfaces).
  SQL stays behind the SearchIndex boundary (Postgres promise intact); the filter is an
  OR of the selected buckets' clauses in the existing format slots.
- **API** (`/api/v1/search`): new `sources=` param, comma-separated tokens from
  {chat, agents, system, all}. **Default: `chat`** (the ratified trim). Unknown token →
  422 problem (never silently ignored). Applies to both scope=global and scope=session.
- **Web room**: human-eyes surface, behavior preserved — the client passes
  `sources=all` explicitly everywhere it searches (search tab, sidebar, in-conversation).
  A room-side scope toggle is a future feature, not this arc.
- **TUI**: search defaults to chat. Trailing flag tokens in the search text widen:
  `--agents`, `--system`, `--all` (stripped before FTS). /help documents it.
- **Skill** (separate deliverable, `~/.claude/skills/`): documents the decision rule and
  this vocabulary for cross-project session use.

## §4 Honesty requirements

- The TUI reports the active sources in its result line when widened (and the count).
- No silent caps: `all` genuinely means all three buckets, nothing else exists to hide.
- `total` semantics unchanged: same filter applied to page and count.
