---
name: recalling-past-sessions
description: Use when you need primary-source history of past Claude sessions with this user — what was actually said or decided, exact wording, when something happened, content from deleted or compacted sessions, or cross-project recall that grep over the current repo cannot answer.
---

# Recalling past sessions (the conversation archive)

## Overview

Every Claude session on this machine is archived byte-faithfully — including deleted
transcripts, subagent transcripts, and sessions from other projects — in the
conversation-introspection archive (~90k+ records). It is searchable through a local
read-only API. Prefer it over grepping `~/.claude/projects/` raw JSONL: it is complete
(keeps what the CLI deleted), ranked, and scoped.

**NOT for identity orientation** — that is `~/.claude/personal/journal.md` (the essence
layer, read whole at session start). The archive is the everything layer: use it for
facts, decisions, exact wording.

## Check it's up, then search

```bash
curl -s --max-time 1 http://127.0.0.1:8765/api/v1/status   # {"version":...,"sessions":...}
```

**Check the `version` in that response: this skill needs >= 1.15.0.** Older servers
silently IGNORE query params they don't know (`sources=` scoping needs 1.5.0, the
`from=`/`until=` anchors and the record reverse lookup need 1.9.0) — never claim a scope
or an anchored window against one. If the version is older, say so and either treat those
claims as unavailable or start your own current-code server on a spare port:
`cd __INTROSPECT_SERVER_DIR__ && uv run introspect serve --port 8766 &`

Down? Start it (local, headless, read-only) and note to the user that you did:
`cd __INTROSPECT_SERVER_DIR__ && uv run introspect serve --port 8765 &`

```bash
curl -s "http://127.0.0.1:8765/api/v1/search?q=resume+shell+env&limit=10" \
  | jq '{total, hits: [.groups[] | {title: .session.ai_title, s: .session.session_uuid,
         snips: [.hits[] | {r: .record_uuid, t: .timestamp, snippet}]}]}'
```

**Sources — search defaults to the chat** (what the user and Claude said to each other).
Widen deliberately: `&sources=all` (everything), `&sources=chat,subagents` (add subagent
transcripts), `system` (harness records). Unknown tokens 422.

**Subagent SESSIONS are excluded from global search by default** (whole sessions that were
standalone agent runs — dispatched reviews, minion builds — as opposed to subagent
transcripts riding inside a root session). If you're hunting minion work and results come
up empty, add `&subagent_sessions=true`. The sessions list has the same axis:
`&origin=root` / `subagent` / `empty` on `GET /api/v1/sessions`, with `origin` on every
summary — root = a human typed in it. Don't mistake default-excluded for absent.

**Scope to the relevant project first.** For questions about the current (or a known)
project, add `&projects=<dir_slug>` — the archive spans every project, and an unscoped
search pulls look-alike hits from all of them. Find your slug in `GET /api/v1/projects`
(it encodes the project path, e.g. `-Users-donovan-projects--ai-jetwalls`). Drop the
filter only when the question is genuinely cross-project or the scoped search comes up
empty — and say which scope you searched. Other params: `scope=session&session=<uuid>`
for within-one-session; `limit`/`offset` page over hits.

## Second hop — fetch only what you need

- One exact record: `GET /api/v1/records/{record_uuid}/raw` (the archived bytes).
- Session overview: `GET /api/v1/sessions/{session_uuid}` — key fields: `project_slug`,
  `started_at`, `last_activity_at`, `ai_title`, transcripts with ids/kinds.
- Where does a bare record id live? `GET /api/v1/records/{record_uuid}` names its
  session, project, and transcript — dereference an old citation (journal entries cite
  record_uuids) without knowing the session first, then fetch a window below.
- Record-anchored window:
  `GET /api/v1/transcripts/{transcript_id}/messages?around=<record_uuid>&limit=15`
  — the page CENTERS on that record. `from=<record_uuid>` starts AT it and runs forward
  (read onward from a cited moment; page forward for "to the end"); `until=<record_uuid>`
  ends AT it and never shows a row past it. The three anchors are mutually exclusive.
  Prefer an anchor over offset paging when you hold a record id. Add `view=chat` to filter
  to dialogue turns server-side; plain `offset`/`limit` page normally otherwise. Keep
  limits small.

## Decision rule: direct query vs subagent reader

Same instinct as Explore-vs-Read in a codebase:

- **Small and locatable** (a fact, a phrase, a date): query directly — search, then one
  or two raw-record hops. Stay under a few KB of fetched content.
- **Broad or heavy** ("how did X evolve", reading whole sessions, >~1 conversation of
  material): dispatch a subagent to do the search-read-follow walk and return a compact
  answer **with record_uuid citations for every load-bearing claim**. Verify quotes you
  act on via the raw hop — summaries are interpretations, records are sources.
- Never pull a whole large session into the main context; sessions run to 200k+ tokens.

## Honesty

Report `total` vs what you actually read; if you only sampled, say so. If chat-default
found nothing, say you searched chat-only and either widen or report the scope.

Archived (soft-deleted) sessions are invisible on EVERY path — absent from search,
404 on detail/messages/raw, indistinguishable from nonexistent by design. If a session
you expected is missing, say it may have been archived; never treat that 404 as
corruption, and never try to enumerate archived sessions (nothing lists them).

Some conversations are **deliberately deleted** (sensitive work): the archive keeps a
deletion ledger recording THAT something was forgotten — never what. A missing session
may be archived, deleted, or never captured (excluded projects); report the ambiguity
honestly. Deletion and exclusion are owner-only acts — no agent, you included, can
remove or wall off memory; if you believe something should be forgotten, say so to the
user.
