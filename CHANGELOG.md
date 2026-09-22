# Changelog

The top entry is the current version. Entries are written for users: what changed
in what you can see and do. Format: `## MAJOR.MINOR.PATCH — YYYY-MM-DD` followed
by `- ` bullets.

## 1.12.0 — 2026-09-20
- Search understands API message ids: type a bare `msg_...` id into the global or in-conversation search and you get that exact message's records — direct lookup, no full-text guessing, in the same result shapes as any other search.
- New API: `GET /api/v1/records/by-message-id/{id}` lists every live record carrying that API message id (one API message can span several records; newest generation of each, archived sessions excluded — all-archived answers 404, same as unknown).
- The archive now stores each assistant message's own API id (`message.id`, the `msg_...` string) at capture; run `introspect reparse` once to backfill your existing history — until then, id lookups only find records captured after this update.

## 1.11.1 — 2026-09-14
- Your own typed messages now render chat-style code fences (opened mid-line, or closed at the end of a line instead of on their own) as real code blocks in the reading room, instead of garbled inline code or paragraphs swallowed by an unclosed block — Claude's own messages and the raw record inspector are unaffected.

## 1.11.0 — 2026-09-10
- New **Memories** tab (`/memories`) in the reading room: browse Claude Code's own per-project auto-memory notes, read live off disk — nothing is captured, indexed, or archived by this feature, and nothing in the tab can edit or delete a memory file.
- Filter memories by text (name, description, body) or by type chip (`user` / `feedback` / `project` / `reference` / others present / `untyped`), plus the same project filter the other tabs share.
- Click a memory's name to expand it as rendered markdown; a copy chip grabs the file's absolute path so you can paste it into a Claude Code session as a citation.
- A memory file that can't be fully read or parsed is still listed, marked `unreadable: <reason>`, never hidden.
- Excluded projects never appear in the Memories tab, indistinguishable from a project with no memories at all — the same owner-only exclusion ceremony that governs capture.

## 1.10.0 — 2026-08-25
- `/session-name <name>` is now fast: the skill runs a shell command *before* the model turn and the model only relays its one-line result — about half a second of work instead of a model narrating curl calls for tens of seconds and thousands of tokens. Re-run `/skill install` to pick it up; the slash command and its behavior are unchanged.
- New CLI command behind it: `introspect session-name [<name> | --stdin] [--session <uuid>] [--url <base>]` names a session in the archive (defaulting to the Claude Code session it runs inside, via `CLAUDE_CODE_SESSION_ID`), importing it first if needed, and prints one line saying what happened — including "server not running" with the start command, and "server too old, restart it" for anything before 1.8.0.

## 1.9.0 — 2026-08-23
- Conversations slice by record now, not just by index: `messages` accepts `from=<record_uuid>` (the window starts at that record and runs forward — "this entry + 5") and `until=<record_uuid>` (ends at that record, never past it), alongside the existing centered `around=`; the three anchors are mutually exclusive.
- New reverse lookup: `GET /api/v1/records/{uuid}` names the session, project, and transcript a bare record id lives in — a citation from an old note dereferences to its conversation without knowing the session first.
- The recall skill teaches both and its version gate now requires 1.9.0 (older servers silently ignore query params they don't know, so the skill refuses to claim scoping or anchoring against one) — re-run `/skill install` to update.

## 1.8.0 — 2026-08-23
- New `session-name` skill: say `/session-name <name>` (or `session-name <name>`) in any Claude Code session and that Claude names the session in the archive — the title you see in the TUI and the reading room — importing the session first if the 15-minute belt hasn't captured it yet. Run `/skill install` in the TUI to pick the skill up.
- The skill never starts anything on its own: if the archive server isn't running, or is running a version older than this one, Claude tells you and stops.
- API: `PUT /api/v1/sessions/{uuid}/title` accepts `?import_if_missing=true` — an unknown session triggers one import (waiting out a running one, up to 30s) before the title is set; a 404 after that names the import run and the likely reasons (excluded project, transcript not yet on disk).

## 1.7.0 — 2026-08-17
- New project exclusion for sensitive work: `/exclude` (TUI) and `introspect exclude` (CLI) wall a project off from capture — imports skip its directory before reading anything beneath it, with an optional reason kept on the entry. Exclude before the sensitive work starts. Owner-only: no API can exclude, list, or reveal exclusions.
- New `/delete` (TUI) and `introspect delete` (CLI): irreversible, ceremonied deletion of a session or whole project — preview first, explicit `yes` to act, and every deletion writes a ledger row with your optional reason (the archive remembers *that* it forgot, never what).
- Deletion's two follow-up asks are separate and never automatic: `backups yes` scrubs backup DB copies (otherwise untouched), and `forbid yes` adds a deleted session to a re-import wall so still-on-disk source files can't silently resurrect it. `/exclude add <uuid>` and `remove` manage that wall openly.

## 1.6.1 — 2026-08-16
- The `recalling-past-sessions` skill now documents the messages endpoint's `around=` centered-window fetch and `view=chat` filter (it wrongly claimed offset pages were the only windowing), and explains that archived sessions 404 by design on every path — run `/skill install` to pick it up.

## 1.6.0 — 2026-08-16
- New `/skill` TUI command distributes the repo's Claude skills to your machine: `recalling-past-sessions` teaches any Claude session, in any project, to search this archive (chat-scoped, project-scoped, with verified citations). Bare `/skill` reports install state; `/skill install` writes/updates `~/.claude/skills/`, rendered for your checkout's location.

## 1.5.0 — 2026-08-15
- Search now defaults to the chat — what you and Claude actually said to each other — instead of everything: subagent transcripts and harness records are excluded until you ask for them. In the TUI, widen with `--agents`, `--system`, or `--all` in the search text; on the API, with `sources=agents,system` or `sources=all`. The web reading room still searches everything, unchanged.

## 1.4.0 — 2026-08-11
- TUI: `/start-web` and `/stop-web` are replaced by `/web [start [public] | stop | status]` — bare `/web` reports server state, matching the `/cron` shape. The old commands are gone (no aliases).
- TUI: server URLs in the log are now interactive — click one to copy it to the clipboard, or cmd+click to open it in terminals that support hyperlinks (iTerm2 and friends).
- TUI: new `/changelog` command — the newest release's changes at a glance, or the whole release history with `/changelog all`.
- TUI: the results/log split is now adjustable — drag the divider between the panels with the mouse, or use alt+↑/alt+↓ (ctrl+shift+↑/↓ also works). Plain Up/Down still navigate results.

## 1.3.1 — 2026-08-11
- Resume links now open the session in an interactive login shell, so per-project environment loaders (direnv and friends) run and MCP servers that need `.env` tokens work the same as when you type `claude --resume` by hand.

## 1.3.0 — 2026-08-10
- Message timestamps now include the date (e.g. `2026.07.19 14:03`), so turns in conversations that cross midnight are no longer ambiguous.
- The session id at the top of a conversation is now click-to-copy — hover to see the full id, click to copy it to your clipboard.

## 1.2.2 — 2026-08-09
- Fully fixed the fresh-install failure on stricter npm versions: the lockfile now carries the required peer packages older npm validates (1.2.1's fix was one half of a pair), and a new test lints the lockfile so an incomplete one can't ship again.
- Updated the `undici` dependency, resolving six npm-audit security findings (1 moderate, 5 high).

## 1.2.1 — 2026-08-09
- Fixed a fresh-install/update failure (`npm ci` reporting "Missing @emnapi/... from lock file") on machines that need the build toolchain's WASM fallback — the lockfile now includes those optional packages.

## 1.2.0 — 2026-08-08
- The reading room and TUI now show which version they're running; the status bar flags when the UI and server versions differ (a stale build is now visible).
- `/update` in the TUI (and `introspect update` in the CLI) checks for new versions, shows what's new, and applies the update — including rebuilding the web UI, the step `git pull` alone never did.
- New `update.sh`: one command to pull the latest version and re-converge.
- Fixed browser caching that could keep showing an old reading room after an update (hard-refresh no longer needed).

## 1.1.0 — 2026-08-08
- Every message now says who was actually talking: YOU only for words you typed; harness-delivered content (tool results, skill injections, dispatch prompts, task notifications) is labeled for what it is.
- Three reading modes — chat, chat+harness, all — replace the "conversation only" toggle.
- The raw-record inspector shows each record's authorship classification.
- The archive tolerates hand-pretty-printed transcript files and heals records previously mis-split by them.

## 1.0.0 — 2026-07-20
- V1: one-command installer, byte-faithful archive with 15-minute cron belt, full-text search, and the Still Water reading room (sidebar search, project filter, editable titles, resume links).
