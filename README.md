# conversation-introspection

A local-first archive and reading room for Claude Code session transcripts. It captures every
line the CLI writes, byte-faithfully, into a SQLite database — before the CLI's own retention
policy deletes them. The database is the system of record; the transcript files on disk are
just an ephemeral feed into it.

## Why

Claude Code writes session transcripts as append-only `.jsonl` files under
`~/.claude/projects/`, and the TUI **actively deletes older sessions** — roughly 30 days of
retention, observed in practice. By the time you go looking for a conversation from a few weeks
ago, it may already be gone. This isn't a caching layer bolted on for convenience; it's an
archive built on the assumption that the source will disappear. Capture has to win the race
against deletion, every time, without exception.

## What is this, in plain words?

If you use Claude Code (Anthropic's terminal AI coding tool), every conversation you have with
it gets written to a log file on your machine, one `.jsonl` file per session, under
`~/.claude/projects/`. That's convenient right up until Claude Code quietly deletes the old
ones — it keeps roughly 30 days of history and then the file is just gone. No warning, no
export prompt, nothing.

If you'd like to keep your conversation history — to search it later, reread something useful
you or Claude said, or just not lose months of context — you need something copying those logs
somewhere safe before deletion happens, automatically, forever. That's what this tool is: a
scheduled job that reads every transcript it can find and archives it into a SQLite database,
byte for byte, with nothing lost or reinterpreted along the way. It also provides full-text
search over every archived conversation, plus a localhost HTTP API (`introspect serve`).

The trust promise, stated plainly: what goes in comes back out identical. Not "close enough" —
identical. The project's flagship test is exactly this: import a transcript, export it back
out, and compare the bytes to the original. That test is described in more detail under
[Development](#development), and it's the bar every change to this codebase has to clear.

## Check if you're affected

Before doing anything else, find out whether you've already lost history to this:

```bash
ls ~/.claude/projects/
```

That lists one directory per project you've used Claude Code in, and inside each, one
`.jsonl` file per session. If you've been using Claude Code for a while but the oldest
session directories you see are noticeably younger than your actual usage — say, you know
you were using it three months ago but nothing in there predates a few weeks — deletion has
already happened. That history is gone; this tool can only protect what's left from here
forward.

## Prerequisites

- macOS or Linux. (Windows is untested; WSL probably works. If you try it, a report — good or
  bad — is welcome; see [How to help](#how-to-help).)
- Python 3.12+.
- [`uv`](https://docs.astral.sh/uv/), the Python package/project manager this repo is built
  around. Install it with either:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  or, on macOS with Homebrew:
  ```bash
  brew install uv
  ```
- Claude Code installed and actually used at least once. If you've never run it, there's
  nothing under `~/.claude/projects/` yet and nothing for this tool to archive.

## Step-by-step first run

1. **Clone the repo and enter the server directory.**
   ```bash
   git clone <this-repo-url>
   cd conversation-introspection/server
   ```

2. **Install dependencies.**
   ```bash
   uv sync
   ```
   This creates a `.venv` inside `server/` and installs everything the project needs — you
   don't need to activate it yourself; `uv run` below does that for you.

3. **Run your first import.**
   ```bash
   uv run introspect import
   ```
   Expect a summary line like:
   ```
   imported files=168 records=19510 dupes=0 anomalies=17494 gone=0 status=ok
   ```
   Your numbers will differ, but expect **something similar in shape**: a nonzero
   `files`/`records` count, and — this is the part that looks alarming the first time —
   possibly a *large* `anomalies` count. That's normal on a first import. It means the
   schema-drift detector (see [Architecture](#architecture)) is meeting your particular Claude
   Code version for the first time and finding fields it doesn't recognize yet. Those
   anomalies are recorded at `info` severity by default — they're notes, not errors — and the
   underlying data is captured either way, in full, unconditionally. Nothing is dropped or
   corrupted because of an anomaly. `status=ok` is the number that actually matters here.

4. **Check what's in the archive.**
   ```bash
   uv run introspect status
   ```
   This prints archive-wide counts and the most recent import run, e.g.:
   ```
   sessions=13 files=168 records=19510 anomalies=17494 (error=0 warn=0 info=17494)
   last run: id=1 trigger=cli status=ok finished_at=2026-07-13 09:00:01
   ```
   The `(error=... warn=... info=...)` breakdown is the important part: on a healthy first run
   you want `error=0`. A wall of `info` is fine.

5. **Prove the byte-faithfulness claim to yourself.** Pick any session UUID from your own
   `~/.claude/projects/<project-slug>/` directory (the filename minus `.jsonl`), then:
   ```bash
   uv run introspect export <session-uuid> -o /tmp/exported.jsonl
   cmp /tmp/exported.jsonl ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
   echo $?
   ```
   `cmp` prints nothing and exits `0` when two files are byte-identical. No output from `cmp` —
   and `echo $?` printing `0` — means the export is a perfect reconstruction of the original.
   That's the whole point of this tool, verified on your own data.

6. **Build the reading-room UI, then serve it.** From the repo root:
   ```bash
   cd web && npm install && npm run build
   cd ../server && uv run introspect serve
   ```
   Open [http://127.0.0.1:8765](http://127.0.0.1:8765) — one process, one port, API and UI
   together.

   A few things worth knowing about the reading room: the sidebar search box matches a
   session's title, its message content, or a session-uuid substring as you type — a
   content-only match shows a highlighted snippet under the title, and the query lives in the
   URL (`?filter=`) so it's shareable. A project chip bar above the sidebar scopes the whole
   app — sidebar, both search tabs, and deep links — to the projects you choose
   (`?projects=slug1,slug2`); it's keyboard-driven: arrow-down opens the list, typing filters
   it, Escape closes the list or clears typed text, and a second quick Escape repeats that when
   either was active — or, when the box was already empty and the list already closed, clears the
   selected projects instead. Click a session's
   title to rename it inline — Enter commits, Escape cancels, and a second quick Escape
   reverts to the original archive title; a small dot next to a renamed title reveals that
   original on hover. A "conversation only" toggle in the reader's sticky header hides system
   messages and tool-call/tool-result blocks while keeping anything you actually typed or
   pasted — pasted content is still something a human said.

## Keep it running (cron, ELI5)

A single manual import only protects what existed at that moment. The transcripts still on
disk keep aging toward deletion, so this needs to run on a recurring schedule — cron is the
simplest way to do that on macOS and Linux.

1. Find the absolute path to `uv`, since cron runs with a minimal environment that won't have
   your shell's `PATH`:
   ```bash
   which uv
   ```
   Note the path it prints (commonly `/opt/homebrew/bin/uv` or `~/.local/bin/uv`).

2. Open your crontab for editing:
   ```bash
   crontab -e
   ```

3. Add a line like this, using **absolute paths** for both `uv` and the repo — replace both
   placeholders with the paths on your machine:
   ```cron
   */15 * * * * cd /path/to/conversation-introspection/server && /path/to/uv run introspect import >> ~/.conversation-introspection/import.log 2>&1
   ```
   Save and exit. `*/15 * * * *` means "run every 15 minutes, every hour, every day" — cron's
   five fields are minute/hour/day-of-month/month/day-of-week, and `*/15` in the minute field
   is shorthand for "every 15th minute." Redirecting output to a log file is optional but
   useful for spot-checking that runs are actually happening.

4. **Verify it's actually working.** Wait past the next scheduled run, then check:
   ```bash
   uv run introspect status
   ```
   and confirm `last run:` shows a recent `finished_at` timestamp — that's cron actually
   invoking the tool, not just a crontab entry that silently fails.

You don't need to worry about overlapping runs: `import` (and `reparse`) take an advisory lock
before touching the database, so if a run is still in progress when the next one fires, the
new one exits cleanly with `status=already_running` instead of racing the first. You also
don't need to manually manage schema upgrades — the database migrates itself to the current
schema on every open, so pulling a newer version of this repo and running it against an
existing archive just works.

## Where your data lives — and a privacy warning

The archive lives at `~/.conversation-introspection/archive.db` by default — deliberately
outside any repo working tree, so `git clean` can never touch it.

Be clear-eyed about what's in that file: **everything**, verbatim, that you have ever typed
into or received from Claude Code in an archived session — including anything you pasted into
a prompt. If you ever pasted an API key, a password, a private message, or anything else
sensitive into a Claude Code conversation, it is sitting in that database in plain form. That
is not a bug in this tool; it's a faithful copy of what Claude Code itself already wrote to
disk, kept around after Claude Code would otherwise have deleted it.

This tool is local-only by design. Nothing it does phones home, uploads, or syncs anywhere —
the archive never leaves your machine unless you move it yourself. Treat `archive.db`, and
anything you `export` out of it, with the same care you'd give a folder of your own private
messages. Don't casually share the `.db` file or paste raw exports into a chat, an issue, or a
support ticket without reviewing what's actually in them first.

## CLI reference

All commands run via `uv run introspect <command>` from `server/`.

| Command | What it does | Notes |
|---|---|---|
| `introspect import` | Discovers and ingests new/changed transcripts | Cron-registrable, advisory-locked, idempotent |
| `introspect status` | Prints archive counts and the last import run | |
| `introspect export <session-uuid> [-o file]` | Reconstructs a session's `.jsonl`, byte-identical to the source | Works even if the source file is gone |
| `introspect reparse` | Rebuilds interpretation from stored raw bytes | For schema updates; takes the same advisory lock as `import` |
| `introspect serve [--db PATH] [--port 8765] [--host 127.0.0.1]` | Serves the `/api/v1` read layer on localhost | The default host `127.0.0.1` is deliberate — binding any other interface exposes your entire conversation history to the network; don't, unless you fully understand the exposure |

Every subcommand accepts `--db <path>` (or `INTROSPECT_DB`) to override the archive location;
`import` also accepts `--source-root <path>` (or `INTROSPECT_SOURCE_ROOT`) to override the
transcript source tree — mainly useful for pointing tests at fixtures.

Exit codes are uniform across all four subcommands:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | ran, but with errors — anomalies recorded, a mid-run failure, a named session/transcript wasn't found, or `reparse` found the advisory lock already held |
| `2` | could not open or migrate the database — nothing ran |

## Troubleshooting

- **`uv: command not found`** — `uv` isn't installed or isn't on `PATH` for the shell/cron
  environment you're in. Reinstall per [Prerequisites](#prerequisites), and for cron
  specifically, use `uv`'s absolute path (`which uv`) rather than relying on `PATH`.
- **Exit code `2`** — the database couldn't be opened or migrated at all; nothing ran. Check
  that `~/.conversation-introspection` exists, is a directory (not a stray file of the same
  name), and is writable by your user. If you passed `--db`/`INTROSPECT_DB`, check that path
  instead.
- **`import` prints `status=already_running`** — another `import` or `reparse` currently holds the
  advisory lock. This is expected behavior, not a failure; the run that found it exits cleanly
  with `status=already_running` (exit `0`) and does nothing further.
- **A large `anomalies` count** — see step 3 of [first run](#step-by-step-first-run): a big pile
  of `info`-severity anomalies right after upgrading Claude Code, or on your very first import,
  is the schema-drift detector doing its job, not damage. Anything at `error` severity is
  different and worth investigating — and worth reporting; see [How to help](#how-to-help).
- **"My session isn't in the archive"** — was the transcript file created or modified *after*
  your last `import` run? Run `uv run introspect import` again; it only ingests what's new or
  changed since the last run.
- **A stale `running` row in `ImportRun` after a crash or a killed process** — harmless. The
  advisory lock is per-process (tied to the running process's file lock, not the database row),
  so a leftover `running` status from a run that never finished cleanly doesn't block the next
  run from proceeding normally.

## How to help

This repo is shared in the hope that people will use it and help build it out. Concretely
useful contributions, roughly in order of how easy they are to make:

- **Schema drift reports.** This is the single most valuable thing you can hand back, and it
  costs you almost nothing. Claude Code's transcript format isn't public or versioned in any
  formal way, and every CLI update can add or rename fields. If `introspect status` shows
  `warn`- or `error`-severity anomalies, or an unusually large `info` family you haven't seen
  mentioned in this repo's issues, open an issue with the anomaly `kind`s involved and your
  Claude Code CLI version. **Do not paste raw transcript content or anomaly `detail` blobs
  without reviewing them first** — `detail` is intended to hold only field *names*, not
  message content, but review before pasting rather than trusting that blind. The schema
  registry this project relies on only grows by exactly these reports.
- **Windows/WSL testing.** Untested territory; see [Prerequisites](#prerequisites).
- **A Postgres storage path** for the same schema, as an alternative to SQLite.

For design context on any of the above, start with the spec:
[`docs/superpowers/specs/2026-07-13-conversation-introspection-design.md`](docs/superpowers/specs/2026-07-13-conversation-introspection-design.md).
The codebase is strictly test-driven — every existing feature was written test-first, and
contributions are expected to come with tests, not be reviewed into having them later.

## Architecture

Full design: [`docs/superpowers/specs/2026-07-13-conversation-introspection-design.md`](docs/superpowers/specs/2026-07-13-conversation-introspection-design.md).

The data model has four layers:

- **Identity** — `projects`, `sessions`, `transcripts`: who this data belongs to, keyed on
  envelope fields (uuid, session id, cwd) that have stayed stable across every observed CLI
  version.
- **Archive (system of record)** — `source_files`, `raw_records`: the exact bytes of every
  ingested line, in order. `export` is just `SELECT raw_line ... ORDER BY line_number`.
- **Interpretation (rebuildable)** — `messages`, `content_blocks`, `token_usage`: raw records
  parsed through a versioned Pydantic schema registry. Entirely derived from the archive layer,
  so it can be thrown away and rebuilt with `reparse` whenever the schema learns something new.
- **User data** — `favorites`: never derived from source data, and never touched by import or
  reparse.

The layering follows one principle throughout: **capture, then interpret.** Raw bytes land in
the archive unconditionally, before any parsing is attempted. Interpretation runs alongside
capture but can never block or lose it — a schema violation becomes a `parse_anomaly`, not a
dropped line. This has already paid off once in production: a CLI update introduced ~17,500
drift anomalies from undeclared fields; once the schema registry learned those fields,
`introspect reparse` rebuilt the affected records from raw bytes already on disk, collapsing
the anomaly count to a 21-row drift floor.

## Roadmap

- **Phase 1 — shipped.** Importer and archive core: capture-then-interpret pipeline, schema
  registry, reparse, byte-faithful export, CLI. Python 3.12, uv, SQLAlchemy, Alembic,
  Pydantic v2. 131 tests, built test-first.
- **Phase 2 — shipped.** FTS5 full-text search, a FastAPI read layer over the archive, and
  favorites. 218 tests total, built test-first.
- **Phase 3 — shipped.** The React reading-room UI in the "Still Water" theme (design mockup:
  [`docs/design/2026-07-13-still-water-mockup.html`](docs/design/2026-07-13-still-water-mockup.html))
  — sidebar, windowed conversation reader, dual search, favorites, subagent drill-in.
- **Phase 4 — shipped.** Sidebar content search, an app-level project filter, editable session
  titles, and a conversation-only reading mode (see [step 6](#step-by-step-first-run) above).
  308 server tests, 230 web tests.
- **Future.** A Postgres storage path for the same schema; recovery of "ghost" sessions (ones
  the TUI deleted before this tool ever ran) from `~/.claude/history.jsonl`.

## Development

```bash
cd server
uv sync
uv run pytest
```

Run a single test file the same way, e.g.:

```bash
uv run pytest tests/test_export_roundtrip.py -q
```

Lint with ruff:

```bash
uv run ruff check .
```

Tests are fixture-driven — real transcripts contain private conversation content and never
enter the repo; synthetic fixtures stand in for every record type and CLI era. The API's
tests live in `tests/test_api_*.py`, one file per router. The flagship test, in
`tests/test_export_roundtrip.py`, is a round trip: import a fixture tree, export it back out,
and byte-compare the result to the source. That test is the project's bar for correctness — if
a change can't survive it, it isn't done.
