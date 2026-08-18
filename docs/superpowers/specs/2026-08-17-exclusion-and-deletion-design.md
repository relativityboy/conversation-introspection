# Project exclusion & reasoned deletion (2026-08-17)

Ratified direction (relativityboy, in-session 2026-08-17): some work (sensitive/
classified) must never enter the archive. Entire PROJECTS are the exclusion unit —
"prevention is insurance, after-the-fact is repair." The deletion record carries a
'reason' spot. Claude's four convictions, ratified in the same exchange:

1. **Prevention outranks deletion** — exclusion-before-capture is the primary tool.
2. **Deletion is honest about its reach** — reports every location touched AND not
   touched (source JSONL, backups/, exports).
3. **Tombstones** — the archive remembers THAT it forgot, never WHAT.
4. **Machines never delete** — a ceremonied human act, CLI/TUI only, never on the API;
   no agent-reachable surface can remove memory.

## §1 Census (2026-08-17)

- **Path→slug encoding**, derived from live `projects` rows: every character outside
  `[A-Za-z0-9-]` becomes `-`; no lowercasing. Verified pairs: `/@ai/` → `--ai-` (both
  `/` and `@` map to `-`), `project_centipede` → `project-centipede`,
  `relativityboy.com` → `relativityboy-com`. Consequence (honest limitation): the CLI's
  scheme collides `project_x`/`project.x`; exclusion is slug-granular, exactly as
  granular as the CLI's own storage.
- Discovery walks `<root>/<slug>/…`; `DiscoveredFile.project_slug` is the dir name.
  `run_import` = discover → capture-per-file → `detect_gone(db, discovered)`.

## §2 Phase A — exclusion (prevention)

- **Storage**: `excluded_projects` (migration 0009): `dir_slug` UNIQUE, `reason` TEXT
  NULL, `created_at`. User-data layer: reparse/import never touch it (same invariant
  family as favorites).
- **Enforcement, zero-read**: `discover(root, excluded=frozenset())` skips an excluded
  slug's directory before reading ANYTHING beneath it (not even filenames or
  agent-meta.json). `run_import` loads the excluded set after DB open and passes it in.
- **`detect_gone` ignores excluded projects' rows** — exclusion must never masquerade
  as source deletion (`gone_at_source` stays truthful).
- **Already-captured data is untouched** by exclusion: prevention going forward only;
  Phase B is the repair tool. The command SAYS this when excluding a project that
  already has sessions.
- **Surfaces**: TUI `/exclude [add <path-or-slug> [reason…] | remove <slug> | list]`
  (bare = list) + CLI `introspect exclude add|remove|list` (scriptable, so the wall
  goes up before the sensitive day starts). `add` accepts a filesystem path (leading
  `/`, `~`, or `.`) and encodes it via the census rule, echoing the resulting slug; or
  a raw slug. Reports prior-capture state honestly.
- Read surfaces (search, room, recall skill) are unaffected: exclusion controls
  capture, not reading. An excluded project with zero captured bytes simply never
  appears anywhere.

## §3 Phase B — deletion (repair)

- **Unit**: session or whole project. TUI/CLI only; NO API route ever (conviction 4).
- **Cascade, FTS-safe order**: de-index content blocks via the external-content
  `'delete'` command FIRST (migration 0002 trap), then interpretation rows
  (content_blocks, token_usage, session_events, messages), archive rows (raw_records,
  parse_anomalies, source_files), user rows (favorites, user_titles,
  archived_sessions), then sessions / project row.
- **Ledger** (`deletion_ledger`, same migration or 0010): `kind` (session|project),
  `target` (uuid or slug), `label` (display title at deletion time — the one humane
  breadcrumb), `reason` TEXT NULL (relativityboy 2026-08-17: "a spot for a reason"),
  `sessions_deleted`, `records_deleted`, `created_at`. Never deleted by anything.
- **Ceremony**: mandatory warning naming irreversibility; bare command previews
  (counts + locations report) and instructs the confirm form; only the explicit
  confirm form deletes. Reason is REQUESTED at confirm time (may be declined — NULL).
- **Scrub honesty**: after row deletion — WAL checkpoint (TRUNCATE) + VACUUM (with a
  size warning; 600MB+ DB), `PRAGMA secure_delete` for the run. The completion report
  lists NOT-touched locations: live source JSONL under `~/.claude/projects/`, `.bak`
  files, `backups/` DB copies, any exports — with paths where known.
- **Backups are a separate, additional ask** (relativityboy 2026-08-17, plan-file
  amendment: "going through backups should be an additional-ask with yes/no option" —
  triple-sure). The main deletion NEVER touches `backups/`; after it completes, the
  report names how many backup DB copies still hold the data and gives the distinct
  backup-scrub invocation. That invocation runs the same cascade + ledger row inside
  each backup DB (temporarily lifting/restoring `uchg` where set — this consciously
  supersedes the 2026-08-08 "copy stays unaltered" ruling, but ONLY under this
  triple-confirmed path; the backup also remembers that it forgot).
- **Resurrection guard — session exclusion list** (relativityboy 2026-08-17, mid-turn
  rulings: "on deletion of a session, there should be a session exclusion list as well
  that a session id can be added to" / "We should have it ask to forbid re-import!"):
  deleting a session whose source JSONL still exists on disk would otherwise be
  RE-CAPTURED by the next import (~15 min under cron) — self-undoing deletion. So:
  a new `excluded_sessions` table (session_uuid PK, reason, created_at), symmetric with
  `excluded_projects` one level down. Discovery skips an excluded session's files by
  FILENAME (uuid is in the name — zero content reads, subagent meta.json included).
  The deletion ceremony ASKS to forbid re-import (a distinct explicit invocation, same
  consent pattern as the backups ask); it is never automatic. NOT a silent suppression
  list: `/exclude` manages it openly — `add <uuid>` forbids, `remove <uuid>` re-allows,
  `list` shows both walls. The ledger still only records; it never blocks.
- **Read surfaces afterward**: absence + tombstone. The recall skill already says
  "may have been archived"; it gains "or deliberately deleted (the ledger records
  that, not what)".

## §4 Invariant amendment (for CLAUDE.md, owner-ratified 2026-08-17 in conversation)

"Byte-faithful archive; capture add-only; source deletion never propagates" gains:
*exclusion and deletion are owner-only, ceremonied acts — no automated process, agent,
or API path ever removes or excludes anything; deliberate removal always leaves a
ledger row (the archive remembers that it forgot).*
