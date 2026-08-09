# Changelog

The top entry is the current version. Entries are written for users: what changed
in what you can see and do. Format: `## MAJOR.MINOR.PATCH — YYYY-MM-DD` followed
by `- ` bullets.

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
