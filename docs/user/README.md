# User guide

This is the practical, task-by-task guide to running **conversation-introspection** — the
local-first archive and reading room for your Claude Code session transcripts. If you just want
the shortest path from a clone to reading your history, start with the
[root README's walkthrough](../../README.md#get-set-up-in-three-steps); the pages here go deeper
on each piece.

New to the project and wondering *why* it exists? Read
[How the archive protects you](how-the-archive-protects-you.md) first — it's the one-page mental
model everything else builds on.

## Pages

| Page | What it covers |
|---|---|
| [Install](install.md) | The `./install.sh` one-command setup, what each step does, what re-running gets you (repair, and picking up changes you pulled), and the manual path if you'd rather run the steps yourself. |
| [The TUI](tui.md) | The interactive terminal UI: searching the archive, and every slash command (`/import`, `/reparse`, `/export`, `/status`, `/unarchive`, `/web`, `/cron`, `/update`, `/changelog`, `/skill`, `/exclude`, `/delete`, `/restart`, `/help`, `/quit`) — including the public-bind warning. |
| [The reading room](reading-room.md) | The web UI: the search box, the project filter, editable titles, conversation-only mode, the raw-record inspector, archiving, and what every "not found" state means. |
| [Keeping it running (cron)](cron.md) | The 15-minute belt that wins the race against deletion: `introspect cron install/status/remove`, the marker line, and migrating off a hand-edited crontab entry. |
| [Export](export.md) | The byte-faithful export guarantee, how to prove it on your own data, and which copy of a transcript export hands back. |
| [Updating](update.md) | `/update` (TUI) and `introspect update` (CLI): check, changelist, apply; `update.sh` for scripts; the status bar's version chip and what a mismatch means; troubleshooting a dirty tree or a diverged branch. |
| [How the archive protects you](how-the-archive-protects-you.md) | The concepts: capture-then-interpret, the deletion race, schema versioning and the drift floor, and the local-only privacy stance. |

## Two surfaces, one archive

Everything reads and writes the same SQLite archive (default
`~/.conversation-introspection/archive.db`). There are two ways to touch it:

- **The CLI** (`introspect import|status|export|reparse|unarchive|cron|update|serve|tui`) —
  scriptable, cron-friendly, and what the scheduled belt uses.
- **The TUI + web reading room** — an interactive terminal front-end that can search the archive
  and launch an in-process web server so you read your conversations in a browser.

The CLI and the TUI run the *same* underlying code (import, reparse, export, status, unarchive are
shared functions, not reimplementations), so their numbers and behavior always match.
