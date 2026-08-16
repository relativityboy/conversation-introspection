# The TUI

The TUI is the interactive terminal front-end. It does two things: it **searches your archive**,
and it runs **slash commands** for everything else (import, export, status, scheduling, and
starting the web reader).

Launch it from `server/`:

```bash
uv run introspect tui
```

Both `--db <path>` (or the `INTROSPECT_DB` env var) and `--source-root <path>` (or
`INTROSPECT_SOURCE_ROOT`) work here, exactly as they do on the CLI — handy for pointing at a
non-default archive or transcript tree.

## Searching

Type any text **with no leading `/`** and the TUI searches your archived conversations as a
full-text query. By default the search covers **the chat only** — what you and Claude said to
each other in main conversations. Widen it with flags anywhere in the search text: `--agents`
(subagent transcripts: minion work, dispatch briefings), `--system` (harness records: task
notifications, skill payloads), or `--all` (everything). The result line names the active
sources whenever the search is widened. In the results list:

- **Up / Down** move the highlight through the results.
- **Enter or Right** both open the highlighted result **at its best-matching message**, in your
  browser. (There's no separate "open the session start" gesture — both keys go to the best hit.)

Opening a result needs a web server, so if one isn't already running the TUI **auto-starts it on
`127.0.0.1`** first. Subagent (sub-session) hits deep-link straight into that subagent's transcript.

## Resizing the panels

The seam between the results list and the log is a **draggable divider** — grab it with the mouse
to move the boundary. From the keyboard, **alt+↑ / alt+↓** grow and shrink the log area
(**ctrl+shift+↑/↓** do the same, for terminals that don't pass alt+arrows through). Bare Up/Down
stay reserved for results navigation. Neither panel can be crushed away — the log keeps at least
3 rows and the results at least 5.

## Slash commands

Type `/help` for the live list, or `/help <command>` for one command's full description, examples,
and caveats. The commands, in the order `/help` lists them:

| Command | What it does |
|---|---|
| `/help [command]` | List every command, or explain one in detail. |
| `/import` | Ingest new/changed transcripts. Runs the *same* import as the CLI/cron entry point (in-process, under the shared advisory lock), on a background worker so the UI stays live. If a cron import already holds the lock it reports `already_running` and does nothing — a no-op, not a failure. |
| `/reparse` | Rebuild interpretation from the stored raw bytes (no source files needed). Takes the same lock as import; reports records reparsed and anomaly counts before/after — the drift-fix loop. |
| `/export <uuid> [path]` | Reconstruct a session's transcript byte-for-byte. With no path, writes `<uuid>.jsonl` into the current directory. An unknown uuid reports a not-found message and writes nothing. See [Export](export.md). |
| `/status` | The running version (first line), then archive counts (sessions, archived, files, records, anomalies by severity), the last import run, the schema line, the in-process web-server state, and the cron line. `archived` is an aggregate count only — no archived identities are ever shown. |
| `/unarchive <uuid>` | Restore an archived session so it's readable again. The uuid must be known out-of-band — by design, nothing lists archived sessions. A uuid that's unknown or simply not archived reports a message and changes nothing. |
| `/web [start [public] \| stop \| status]` | Manage the in-process web server. Bare (or `status`), reports its state. `start` binds `127.0.0.1:8765`; `start public` binds `0.0.0.0` instead — see the warning below. If the port is already held by another process, start refuses cleanly. `stop` stops the server the TUI started (exiting the TUI stops it too). The server URL in the log is interactive: click it to copy, ⌘-click to open it (in terminals that support hyperlinks, e.g. iTerm2). |
| `/cron [install [minutes] \| remove]` | Schedule or unschedule periodic imports via your user crontab. See [Keeping it running (cron)](cron.md) for the full story. |
| `/update [yes]` | Bare, checks `origin` and prints the pending changelist without changing anything. `/update yes` applies it — runs `update.sh`, restarts the web server if one was running, and prints a `/restart` hint if server code changed. See [Updating](update.md) for the full story. |
| `/changelog [all]` | Show the newest release entry — what the version you're running changed — plus a count of older entries. `all` prints the entire release history, newest first. || `/restart` | Relaunch the TUI as a fresh process, so code an `/update yes` just applied actually loads (a same-process reload would keep the old code in memory). |
| `/quit` | Exit the app (stopping any web server it started). Ctrl-C does the same. |

## A warning about public bind

`/web start public` (and the CLI's `introspect serve --host 0.0.0.0`) binds the web server to a
network-facing interface. **The archive has no authentication.** A public bind makes every captured
message — everything you and Claude have ever said in an archived session — readable by anyone who
can reach your machine on that port.

Because of that, `/web start public` prints a mandatory warning *before* it even attempts the bind,
so you see the risk regardless of whether the start then succeeds. Don't use it unless you fully
understand the exposure. The default `127.0.0.1` bind keeps the reader on localhost only, which is
what you want almost always.

## The `/cron` caveat: cron runs silently

`/cron install` edits the *same* user crontab that `crontab -e` shows. Once installed, cron runs
the import job **silently** — on macOS there's no prompt and no notification when it fires. To
confirm it's actually running, use `/cron` with no argument (or `introspect cron status`), or check
`/status`'s `last run:` line for a recent finish time. Output from the scheduled job is appended to
`~/.conversation-introspection/cron.log`.
