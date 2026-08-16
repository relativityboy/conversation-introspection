# Changelog

The top entry is the current version. Entries are written for users: what changed
in what you can see and do. Format: `## MAJOR.MINOR.PATCH — YYYY-MM-DD` followed
by `- ` bullets.

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
