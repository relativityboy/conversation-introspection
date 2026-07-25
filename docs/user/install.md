# Install

The fastest way to set up conversation-introspection is the installer at the repo root:

```bash
git clone <this-repo-url>
cd conversation-introspection
./install.sh
```

`install.sh` is an **orchestrator**: it does no install work of its own. Every step shells out to
a tested tool (`uv`, `npm`, the `introspect` CLI) and reports honestly what happened. Running it
is safe to repeat — see [Idempotency and resuming](#idempotency-and-resuming) below.

## What it does, in order

1. **Preflight.** Confirms you're on macOS or Linux and that `git`, `curl`, `node`, and `npm` are
   available. Anything missing is reported *with an install hint*, and the run stops before it
   changes anything. If `uv` (the Python package manager this project uses) is missing, the
   installer **offers** to install it via its official installer — with an explicit yes/no prompt,
   never silently.
2. **`cd server && uv sync`** — creates the Python virtual environment (`server/.venv`) and
   installs the server's dependencies.
3. **`cd web && npm ci`** — installs the web build's dependencies (`web/node_modules`).
4. **`cd web && npm run build`** — builds the reading-room UI into `web/dist`.
5. **`cd server && uv run introspect import`** — runs your first archive import. (An empty source
   tree is fine; a first import with nothing to ingest still succeeds.)
6. **Prints next steps** — how to open the TUI, install the cron belt, and start the web reader.

## Flags

| Flag | Effect |
|---|---|
| `--yes`, `-y` | Non-interactive: accept every prompt (currently just the uv-install consent). Use this in scripts or CI. |
| `--skip-import` | Skip step 5. Useful if you want to set up the tooling now and import later. |
| `--help`, `-h` | Print usage and exit. |

## Idempotency and resuming

Each step is gated on the **real artifact it produces**, not a marker file. That's a deliberate
honesty choice: if you delete `server/.venv`, the next run rebuilds it; a stamp file would have
lied and skipped it.

| Step | Considered done when… |
|---|---|
| `uv sync` | `server/.venv/` exists |
| `npm ci` | `web/node_modules/` exists |
| `npm run build` | `web/dist/index.html` exists |
| first import | the archive DB exists (`$INTROSPECT_DB`, else `~/.conversation-introspection/archive.db`) |

So a second run is a no-op that prints an "already done" line for each completed step, and a run
that **failed partway through resumes at the failed step** — the earlier steps are detected and
skipped. When a step fails, the installer tells you *which* step, shows the tail of that tool's
output (the *why*), and reminds you that re-running resumes there.

## The manual path

You never *need* the installer. If you'd rather run the steps yourself (or the installer isn't a
good fit for your environment), the same sequence by hand is:

```bash
cd server && uv sync
cd ../web && npm ci && npm run build
cd ../server && uv run introspect import
```

See the [root README's prerequisites](../../README.md#prerequisites) for how to install `uv`, and
[Keeping it running (cron)](cron.md) for the scheduled belt you'll want next.

## Environment variables

Every subcommand and the web server accept a few optional environment variables to override defaults:

| Variable | What it does |
|---|---|
| `INTROSPECT_DB` | Archive database file path. Default: `~/.conversation-introspection/archive.db`. |
| `INTROSPECT_SOURCE_ROOT` | Root of the transcript file tree. Default: `~/.claude/projects/`. Used for testing and custom setups. |
| `INTROSPECT_TERMINAL_APP` | Terminal application opened by resume links (macOS). Default: `Terminal`. |
