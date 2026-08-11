# Pretty-Printed JSONL Compat — Design Spec

**Date:** 2026-08-05
**Status:** draft, awaiting owner review
**Relates to:** `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` (capture-then-interpret, §export guarantee); `docs/dev/README.md` (schema-version + reparse workflow); `docs/user/how-the-archive-protects-you.md`

## 1. Overview and motivation

A transcript file was hand-edited by its owner during debugging: its early ~15k lines hold
**pretty-printed multi-line JSON records** (2-space indent, one field per line) instead of the
CLI's compact one-record-per-line JSONL. The capture reader's line-per-record contract shattered
each such record into unparseable fragments — 30,083 `invalid_json` + 48 `unknown_record_type`
anomalies, 99.9% of the archive's anomaly count, all from that one file. Hand-editing transcripts
is a legitimate owner activity; the archive should tolerate its most natural form.

Three deliverables:

- **A.** A **tolerant capture reader**: pretty-printed multi-line records reassemble into single
  raw records at capture time. Boundary decision (owner, 2026-08-05): brace-balanced
  pretty-printed JSON only — no broader corruption tolerance.
- **B.** An **`introspect recapture` command** that heals an already-shattered file in the
  archive, gated by a byte-reconciliation proof.
- **C.** **`introspect-schema/5`**: declare the 9 forward-drift field names behind the 24
  `unknown_field` anomalies and reparse — the established schema/2→3→4 mechanism.

End state (actual, verified post-heal): anomaly count fell from 30,160 to **15,016** after the
primary heal + schema/5 reparse (a later schema/6 bump, driven by a final-review residual, drives
the remaining unknown_field/unknown_record_type floor to 0). Of the 15,016: 40 sit on LIVE
(active) generations — 28 on the affected session's current main-transcript generation, 9 on its
subagent transcript, 3 unrelated single anomalies elsewhere in the archive, unconnected to this
incident. The other **14,974** sit on that same session's earlier generation, which recapture
healed once and which then DIVERGED a second time (the live file changed again before the
archive's next cron cycle) — freezing the pre-heal shattered capture as permanent, superseded
audit history rather than leaving it healed. Its disposition — scope the anomaly census to active
generations only, accept the frozen count as permanent by design, or build an in-archive re-split
path for frozen generations — is an explicit owner decision this plan does not make.
*(Amended 2026-08-06 post-heal: see final review, residual 3.)*

**Resume decision (owner, 2026-08-05): no change.** Restore/resume remains byte-exact
concatenation. A hand-edited session may not be consumable by `claude --resume` (the CLI expects
compact JSONL); one sentence in the resume doc records this as a known, accepted property.

## 2. Tolerant capture reader (A)

Location: the capture-phase line reader (`server/src/introspect/ingest/capture.py`'s record
iterator). Behavior:

- **Fast path unchanged, byte-for-byte.** A line that parses as JSON is one record, exactly as
  today. Compact files never enter the fallback; their captured bytes, record boundaries, and
  hashes are identical to the current implementation's.
- **Fallback on parse failure:** when a line fails `json.loads`, attempt reassembly starting at
  that line: accumulate subsequent lines while tracking brace/bracket balance with a
  string-and-escape-aware scanner; when balance returns to zero, `json.loads` the accumulated
  buffer. Success → ONE raw record whose `raw_line` is the **exact consumed bytes** including
  internal newlines (trailing-newline handling identical to the single-line convention).
- **Bounded:** reassembly gives up after a cap (1,000 lines or 1 MiB per record, whichever
  first), when the buffer's balance goes negative, or when the next fetched line is itself
  independently valid JSON. On give-up, fall back to the CURRENT behavior for the
  originally-failing line only (one `invalid_json` anomaly for that line; the scanner resumes
  at the next line) — tolerance must never turn true corruption into a runaway join, and must
  never mis-join two independently-valid records (the fallback only triggers on a line that
  already failed alone). *(Amended 2026-08-05 during implementation: third give-up trigger —
  an unindented, independently-valid next line ends reassembly early; precondition: native
  records start at column 0, pretty continuations are indented — json.dumps(indent=2)
  output.)*
- **Provenance marker:** a reassembled record's anomaly-free capture carries a per-record flag
  (`reassembled: true` in the raw record's capture metadata, wherever the existing per-record
  bookkeeping lives) so the census can always distinguish native from reassembled records. No
  schema change to interpretation models.
- **Export covenant:** `export.jsonl` concatenates `raw_line` bytes verbatim, so a reassembled
  file exports byte-identically to its on-disk source — pretty printing and all. The round-trip
  test gains a pretty-printed fixture asserting exactly this.
- **Prefix-extension model:** unaffected. The pretty region is static history; incremental
  capture of appended compact lines proceeds as today. The prefix-hash comparison operates on
  bytes, not record counts, and reassembly does not change any byte.

## 3. `introspect recapture` (B)

A capture-layer repair command — the first tool permitted to rewrite a file's raw records — and
therefore maximally paranoid:

- **Invocation:** `introspect recapture <session-uuid>`, explicit, one file per run, never part
  of `import` or cron. *(Amended 2026-08-06: --file variant not built — session-uuid resolution
  covers the need.)*
- **The gate (byte reconciliation):** re-split the source file with the tolerant reader; the
  swap proceeds ONLY if `concat(new raw_lines) == concat(current stored raw_lines for the
  file)` — the archive's stored bytes are conserved exactly, only their record boundaries move.
  On mismatch (e.g., the source file changed beyond its captured prefix in ways capture hasn't
  ingested yet): refuse, print a diagnosis, change nothing. `--dry-run` prints the would-be
  before/after record and anomaly counts.
- **The swap (transactional):** within one transaction — delete the file's raw records, their
  interpretation rows (messages/blocks), and their interpretation-class anomalies (the same
  `_INTERPRETATION_ANOMALY_KINDS` reparse owns); insert the new raw records; re-run
  interpretation over them (the existing `parse_line` machinery). Capture-phase bookkeeping
  anomalies (`source_diverged`, `source_reappeared`) are NEVER deleted — same rule reparse
  enforces, same reason.
- **Identity:** record UUIDs come from record content exactly as at first capture, so
  reassembled records adopt the identities their fragments never had; sessions/transcripts
  linkage rebuilds through the normal interpretation path. FTS entries for the file's messages
  rebuild with interpretation (same triggers/mechanism reparse relies on).
- **Reporting:** before/after counts (raw records, messages, anomalies by kind) printed and
  recorded as an import-run-style row (`trigger: 'recapture'`) so the room's status/import
  history shows the healing event honestly.

## 4. `introspect-schema/5` (C)

Per the schema/2→3→4 precedent and `DIFF_NOTES` convention: declare the 9 verified forward-drift
fields — `interruptedByShutdown`, `source`, `userFeedback`, `isAbortedMidStream`,
`pendingWorkflowCount`, `logicalParentUuid`, `compactMetadata`, `isVisibleInTranscriptOnly`,
`isCompactSummary` — at their observed locations on `UserRecord`/`AssistantRecord`
(`server/src/introspect/schema/v1.py`), append the `DIFF_NOTES` entry naming CLI 2.1.219/2.1.220
as the source versions, bump `SCHEMA_VERSION` to `introspect-schema/5`, and run
`introspect reparse`. Expected: the 24 `unknown_field` anomalies fall to ~0, re-stamped records
carry the new version — asserted the same way `test_schema_versions.py` pins the prior bumps.

## 5. Non-goals

- **No source-file writes, ever** — recapture reads sources, writes only the archive.
- **No resume/restore changes** — byte-exact restore stands (owner decision; §1).
- **No tolerance beyond brace-balanced pretty JSON** — not truncated lines, not interleaved
  text, not concatenated-records-on-one-line (`{...}{...}` on one line does NOT parse and
  remains out of scope — it hits the reader's "balanced but still not valid JSON" give-up
  trigger, not a fast path). *(Corrected 2026-08-06.)*
- **No automatic recapture** — healing is an explicit owner action per file.

## 6. Testing

- **Reader:** fixture file modeled on the real incident — pretty-printed head (several records,
  varied nesting, strings containing braces/escapes/`}` at line starts) + compact tail; assert
  record count, contents, `reassembled` flags, and anomaly count 0. Give-up cases: unbalanced
  garbage (cap + resume-at-next-line), negative balance, over-cap record. Regression: a fully
  compact fixture captures with byte-identical raw records vs the current reader (golden
  comparison).
- **Export:** round-trip the pretty fixture — `export.jsonl` byte-identical to source (the
  flagship guarantee, extended to the new shape).
- **Recapture:** end-to-end heal of a shattered fixture (import with OLD splitting semantics
  simulated by fixture construction → recapture → anomalies fall, messages appear, FTS finds
  them); the refusal path (stored bytes ≠ re-split bytes → no mutation, exit non-zero);
  idempotence (second run is a no-op); bookkeeping-anomaly preservation.
- **Schema/5:** per-field fixture lines + reparse floor assertion, mirroring the existing
  version-bump tests.

## 7. Documentation

- `docs/user/how-the-archive-protects-you.md`: a short "hand-edited transcripts" note (tolerated
  at capture; healed by recapture; exported byte-identically).
- `docs/user/export.md`: pretty-printed sessions export exactly as stored.
- Resume doc (`docs/user/reading-room.md` §resume): one sentence — hand-edited sessions restore
  byte-exact and may not be consumable by `claude --resume`.
- `docs/dev/README.md`: recapture added beside the reparse workflow description, with the
  byte-reconciliation gate called out.
