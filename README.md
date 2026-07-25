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
search over every archived conversation, plus a localhost reading room you open in your browser.

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
- Node.js + npm (for building the reading-room UI).
- Claude Code installed and actually used at least once. If you've never run it, there's
  nothing under `~/.claude/projects/` yet and nothing for this tool to archive.

You don't have to install these by hand or check them yourself — the installer in the next
section verifies each one and tells you exactly what's missing (with an install hint) before it
changes anything. It also handles [`uv`](https://docs.astral.sh/uv/), the Python package manager
this project is built around: if `uv` isn't installed, the installer *offers* to install it for
you via its official installer, asking first.

## Get set up in three steps

The installer does all the install work — dependencies, the web build, and your first import —
so the path from a fresh clone to reading your history is short.

### 1. Clone and run the installer

```bash
git clone <this-repo-url>
cd conversation-introspection
./install.sh
```

`install.sh` checks your machine has what it needs, then runs the whole setup: it creates the
Python environment (`uv sync`), installs and builds the reading room (`npm ci && npm run build`),
and runs your first archive import. **Re-running it is always safe** — completed steps are
detected and skipped, so if any step fails you just fix the cause and run `./install.sh` again and
it resumes where it left off. (Prefer to do it by hand, or want the details? See
[`docs/user/install.md`](docs/user/install.md). Flags: `--yes` to accept every prompt
non-interactively, `--skip-import` to set up the tooling without importing yet.)

Your first import prints a summary line like:

```
imported files=168 records=19510 dupes=0 anomalies=17494 gone=0 status=ok
```

Your numbers will differ, but expect **something similar in shape**: a nonzero `files`/`records`
count, and — this is the part that looks alarming the first time — possibly a *large* `anomalies`
count. That's normal on a first import. It means the schema-drift detector (see
[Architecture](#architecture)) is meeting your particular Claude Code version for the first time
and finding fields it doesn't recognize yet. Those anomalies are recorded at `info` severity by
default — they're notes, not errors — and the underlying data is captured either way, in full,
unconditionally. Nothing is dropped or corrupted because of an anomaly. `status=ok` is the number
that actually matters here.

### 2. Open the TUI and start the belt

The archive only protects what an import actually *catches* — and the transcripts on disk keep
aging toward deletion. So the single most important thing to do next is put imports on a schedule.
The TUI makes that one command. Launch it:

```bash
cd server && uv run introspect tui
```

Then, inside the TUI, type:

```
/cron install
```

That schedules an import every 15 minutes — the tightest interval that matters, the gap between
"you finished a conversation" and "it's safely archived," and comfortably inside the ~30-day
deletion window. Prefer the CLI? `uv run introspect cron install` does exactly the same thing
(on macOS, expect a one-time OS permission prompt the first time you run a `cron` command — see
[Keep it running](#keep-it-running-the-15-minute-belt)).
One heads-up: cron runs the job **silently** (no prompt, no notification), so verify it later —
see [Keep it running](#keep-it-running-the-15-minute-belt).

### 3. Read your conversations

Still in the TUI, type:

```
/start-web
```

and open [http://127.0.0.1:8765](http://127.0.0.1:8765) — one process, one port, API and UI
together. (Standalone equivalent: `uv run introspect serve`.)

A few things worth knowing about the reading room — full details in
[`docs/user/reading-room.md`](docs/user/reading-room.md):

- The **sidebar search box** matches a session's title, its message content, or a session-uuid
  substring as you type; a content-only match shows a highlighted snippet under the title, and
  the query lives in the URL (`?filter=`) so it's shareable.
- A **project chip bar** above the sidebar scopes the whole app — sidebar, search, and deep
  links — to the projects you choose (`?projects=slug1,slug2`). It's keyboard-driven: arrow-down
  opens the list, typing filters it, and Escape is layered so it never nukes a selection by
  accident — a single Escape closes the list only; a quick second Escape then clears your typed
  text (or, if the box was already empty and the list closed, clears the selected projects).
- Click a session's title to **rename it inline** — Enter commits, Escape cancels, and a quick
  second Escape reverts to the original archive title; a small dot next to a renamed title reveals
  that original on hover.
- A **"conversation only"** toggle in the reader's header hides system messages and
  tool-call/tool-result blocks, keeping the human-and-assistant conversation (including anything
  you pasted, which is still something a human said). It's remembered across sessions.
- Every message row carries a small mono **`{}`** that opens a **raw-record inspector**: the exact
  stored transcript line, pretty-printed with a raw-bytes toggle, and ◀/▶ (or the arrow keys) to
  step through neighbouring records.
- `⟲ resume` — reopen any archived conversation in a terminal via `claude --resume`, restoring the
  transcript first if Claude Code deleted it.

### Prove the trust promise to yourself

Pick any session UUID from your own `~/.claude/projects/<project-slug>/` directory (the filename
minus `.jsonl`), then:

```bash
cd server
uv run introspect export <session-uuid> -o /tmp/exported.jsonl
cmp /tmp/exported.jsonl ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
echo $?
```

`cmp` prints nothing and exits `0` when two files are byte-identical. No output from `cmp` — and
`echo $?` printing `0` — means the export is a perfect reconstruction of the original. That's the
whole point of this tool, verified on your own data. More on the guarantee:
[`docs/user/export.md`](docs/user/export.md). And any time you want the archive's counts and the
last import run, `uv run introspect status` (or `/status` in the TUI) prints them.

## Keep it running (the 15-minute belt)

A single manual import only protects what existed at that moment. The transcripts still on disk
keep aging toward deletion, so this needs to run on a recurring schedule — cron is the simplest
way to do that on macOS and Linux.

### The easy way: `/cron install` (TUI) or `introspect cron install` (CLI)

You already did this in [step 2](#2-open-the-tui-and-start-the-belt) if you followed the arc. The
tool manages its own crontab entry for you, from either surface:

```bash
uv run introspect cron install          # schedule an import every 15 minutes
uv run introspect cron install --every 5   # or pick your own interval (1–60 minutes)
uv run introspect cron status           # show whether it's scheduled, and the exact line
uv run introspect cron remove           # unschedule it
```

`install` adds (or replaces) exactly **one** line in your user crontab, marked so it can find and
manage only its own entry — every other crontab line you have is preserved untouched. It schedules
the absolute path to the `introspect` binary directly (no `uv` or `cd` needed, so it survives
cron's minimal environment), and appends output to `~/.conversation-introspection/cron.log`.
Running it again just updates the interval; it never creates a second job.

One caveat worth knowing: cron runs the job **silently** — on macOS there's no prompt and no
notification when it fires. Use `introspect cron status` (or `introspect status`, and check
`last run:`) to confirm it's actually running.

A separate, one-time thing: the *first* `cron` command you run on macOS may trigger an OS
permission dialog (confirming your terminal can manage scheduled jobs) — that's expected, not an
error. See [`docs/user/cron.md`](docs/user/cron.md#the-macos-permission-prompt) for details.

### Or manage cron yourself

If you'd rather hand-edit your crontab (for example, to run through `uv` from the repo instead of
the installed binary), you can. Find the absolute path to `uv` (cron has a minimal `PATH`) with
`which uv`, open your crontab with `crontab -e`, and add a line using **absolute paths** for both
`uv` and the repo:

```cron
*/15 * * * * cd /path/to/conversation-introspection/server && /path/to/uv run introspect import >> ~/.conversation-introspection/import.log 2>&1
```

`*/15 * * * *` means "run every 15 minutes" — cron's five fields are
minute/hour/day-of-month/month/day-of-week, and `*/15` in the minute field is shorthand for "every
15th minute." Wait past the next scheduled run and confirm it's working with `uv run introspect
status` (`last run:` should show a recent `finished_at`).

**Migration note:** a hand-edited line is **not** recognized by the managed commands. `introspect
cron status` and `install`/`remove` only see the tool's *own* marked line, so a line you added
yourself makes `cron status` report "not installed" even while it's happily importing — and a later
`cron install` would add a *second*, managed line alongside it. To switch to the managed line,
**remove your manual line first** (`crontab -e`), then run `introspect cron install`. Full details:
[`docs/user/cron.md`](docs/user/cron.md).

You don't need to worry about overlapping runs: `import` (and `reparse`) take an advisory lock
before touching the database, so if a run is still in progress when the next one fires, the new one
exits cleanly with `status=already_running` instead of racing the first. You also don't need to
manually manage schema upgrades — the database migrates itself to the current schema on every open,
so pulling a newer version of this repo and running it against an existing archive just works.

## Where your data lives — and a privacy warning

The archive lives at `~/.conversation-introspection/archive.db` by default — deliberately
outside any repo working tree, so `git clean` can never touch it.

Be clear-eyed about what's in that file: **everything**, verbatim, that you have ever typed into
or received from Claude Code in an archived session — including anything you pasted into a prompt.
If you ever pasted an API key, a password, a private message, or anything else sensitive into a
Claude Code conversation, it is sitting in that database in plain form. That is not a bug in this
tool; it's a faithful copy of what Claude Code itself already wrote to disk, kept around after
Claude Code would otherwise have deleted it.

This tool is local-only by design. Nothing it does phones home, uploads, or syncs anywhere —
the archive never leaves your machine unless you move it yourself. The one place you can *choose*
to expose it is binding the web server to a public interface (`/start-web public`, or
`serve --host 0.0.0.0`), which the tool refuses to do quietly — it prints a mandatory no-auth
warning first, because the archive has no authentication and a public bind makes every captured
message readable by anyone on your network. Treat `archive.db`, and anything you `export` out of
it, with the same care you'd give a folder of your own private messages.

## CLI reference

All commands run via `uv run introspect <command>` from `server/`.

| Command | What it does | Notes |
|---|---|---|
| `introspect import` | Discovers and ingests new/changed transcripts | Cron-registrable, advisory-locked, idempotent |
| `introspect status` | Prints archive counts and the last import run | |
| `introspect export <session-uuid> [-o file]` | Reconstructs a session's `.jsonl`, byte-identical to the source | Works even if the source file is gone |
| `introspect reparse` | Rebuilds interpretation from stored raw bytes | For schema updates; takes the same advisory lock as `import` |
| `introspect unarchive <session-uuid>` | Restores an archived session so it's readable again | The **only** way to unarchive; the UI never lists or reveals archived sessions, so you supply the uuid out-of-band |
| `introspect cron status\|install [--every N]\|remove` | Manages a single scheduled-import line in your user crontab | Owns exactly one marked line (default every 15 min, `--every` accepts 1–60); replaces rather than duplicates, and leaves every other crontab entry byte-for-byte untouched |
| `introspect serve [--db PATH] [--port 8765] [--host 127.0.0.1]` | Serves the API + reading-room UI on localhost | The default host `127.0.0.1` is deliberate — binding any other interface exposes your entire conversation history to the network; don't, unless you fully understand the exposure |
| `introspect tui [--db PATH] [--source-root PATH]` | Interactive terminal UI: type to search the archive, or run slash commands (`/import`, `/reparse`, `/export`, `/status`, `/unarchive`, `/start-web [public]`, `/stop-web`, `/cron [install [minutes] \| remove]`, `/help`, `/quit`) | Search results open in your browser (**Enter or Right** both open the best-matching message), auto-starting an in-process web server on `127.0.0.1:8765`; `/start-web public` binds `0.0.0.0` and prints a mandatory no-auth warning first |

Every subcommand accepts `--db <path>` (or `INTROSPECT_DB`) to override the archive location;
`import` also accepts `--source-root <path>` (or `INTROSPECT_SOURCE_ROOT`) to override the
transcript source tree — mainly useful for pointing tests at fixtures. The full command-by-command
walkthrough is in [`docs/user/tui.md`](docs/user/tui.md).

Exit codes are uniform across all subcommands:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | ran, but with errors — anomalies recorded, a mid-run failure, a named session/transcript wasn't found, or `reparse` found the advisory lock already held |
| `2` | could not open or migrate the database — nothing ran |

## Troubleshooting

- **`uv: command not found`** — `uv` isn't installed or isn't on `PATH` for the shell/cron
  environment you're in. Reinstall per [Prerequisites](#prerequisites) (or let `./install.sh` offer
  to), and for cron specifically, use `uv`'s absolute path (`which uv`) rather than relying on
  `PATH`.
- **The web reader says "UI: not built (API only)"** — the reading room hasn't been built yet. Run
  `cd web && npm run build` (the installer does this for you) and restart `serve`.
- **Exit code `2`** — the database couldn't be opened or migrated at all; nothing ran. Check
  that `~/.conversation-introspection` exists, is a directory (not a stray file of the same
  name), and is writable by your user. If you passed `--db`/`INTROSPECT_DB`, check that path
  instead.
- **`import` prints `status=already_running`** — another `import` or `reparse` currently holds the
  advisory lock. This is expected behavior, not a failure; the run that found it exits cleanly
  with `status=already_running` (exit `0`) and does nothing further.
- **A large `anomalies` count** — see [step 1](#1-clone-and-run-the-installer): a big pile
  of `info`-severity anomalies right after upgrading Claude Code, or on your very first import,
  is the schema-drift detector doing its job, not damage. Anything at `error` severity is
  different and worth investigating — and worth reporting; see [How to help](#how-to-help).
- **`cron status` says "not installed" but imports are happening** — you have a hand-edited crontab
  line, which the managed commands don't recognize. See the
  [migration note](#or-manage-cron-yourself).
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
[`docs/superpowers/specs/2026-07-13-conversation-introspection-design.md`](docs/superpowers/specs/2026-07-13-conversation-introspection-design.md),
and the [developer guide](docs/dev/README.md). The codebase is strictly test-driven — every
existing feature was written test-first, and contributions are expected to come with tests, not
be reviewed into having them later.

## Documentation

- **[User guide](docs/user/README.md)** — task-by-task: [install](docs/user/install.md), the
  [TUI](docs/user/tui.md), the [reading room](docs/user/reading-room.md),
  [cron](docs/user/cron.md), [export](docs/user/export.md), and the one-page concept model,
  [How the archive protects you](docs/user/how-the-archive-protects-you.md).
- **[Developer guide](docs/dev/README.md)** — architecture pointer, running the tests, and the
  schema-extension loop.

## Architecture

Full design: [`docs/superpowers/specs/2026-07-13-conversation-introspection-design.md`](docs/superpowers/specs/2026-07-13-conversation-introspection-design.md).
The plain-language version for users is
[How the archive protects you](docs/user/how-the-archive-protects-you.md).

The data model has four layers:

- **Identity** — `projects`, `sessions`, `transcripts`: who this data belongs to, keyed on
  envelope fields (uuid, session id, cwd) that have stayed stable across every observed CLI
  version.
- **Archive (system of record)** — `source_files`, `raw_records`: the exact bytes of every
  ingested line, in order. `export` is just `SELECT raw_line ... ORDER BY line_number`.
- **Interpretation (rebuildable)** — `messages`, `content_blocks`, `token_usage`: raw records
  parsed through a versioned Pydantic schema registry. Entirely derived from the archive layer,
  so it can be thrown away and rebuilt with `reparse` whenever the schema learns something new.
- **User data** — `favorites`, custom titles, the archived-session set: never derived from source
  data, and never touched by import or reparse.

The layering follows one principle throughout: **capture, then interpret.** Raw bytes land in
the archive unconditionally, before any parsing is attempted. Interpretation runs alongside
capture but can never block or lose it — a schema violation becomes a `parse_anomaly`, not a
dropped line. This has already paid off once in production: a CLI update introduced ~17,500
drift anomalies from undeclared fields; once the schema registry learned those fields,
`introspect reparse` rebuilt the affected records from raw bytes already on disk, collapsing
the anomaly count to a small drift floor.

## Roadmap

- **Phase 1 — shipped.** Importer and archive core: capture-then-interpret pipeline, schema
  registry, reparse, byte-faithful export, CLI. Python 3.12, uv, SQLAlchemy, Alembic,
  Pydantic v2. Built test-first.
- **Phase 2 — shipped.** FTS5 full-text search, a FastAPI read layer over the archive, and
  favorites. Built test-first.
- **Phase 3 — shipped.** The React reading-room UI in the "Still Water" theme (design mockup:
  [`docs/design/2026-07-13-still-water-mockup.html`](docs/design/2026-07-13-still-water-mockup.html))
  — sidebar, windowed conversation reader, dual search, favorites, subagent drill-in.
- **Phase 4 — shipped.** Sidebar content search, an app-level project filter, editable session
  titles, a conversation-only reading mode, and production serving (the API and built UI on one
  port).
- **V1 — shipped.** A one-command installer (`./install.sh`), a thorough getting-started
  walkthrough, and a full user + developer documentation set under [`docs/`](docs/). Suites at
  471 server + 267 web tests, every feature built test-first.
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

The web reading room has its own suite:

```bash
cd web
npm ci
npm test        # vitest
npm run lint
npm run build
```

Tests are fixture-driven — real transcripts contain private conversation content and never
enter the repo; synthetic fixtures stand in for every record type and CLI era. The API's
tests live in `tests/test_api_*.py`, one file per router. The flagship test, in
`tests/test_export_roundtrip.py`, is a round trip: import a fixture tree, export it back out,
and byte-compare the result to the source. That test is the project's bar for correctness — if
a change can't survive it, it isn't done. The [developer guide](docs/dev/README.md) has the full
picture, including the schema-extension loop.
