# Install

The fastest way to set up conversation-introspection is the installer at the repo root:

```bash
git clone <this-repo-url>
cd conversation-introspection
./install.sh
```

`install.sh` is an **orchestrator**: it does no install work of its own. Every step shells out to
a tested tool (`uv`, `npm`, the `introspect` CLI) and reports honestly what happened. Running it
is safe to repeat, and repeating it is also how you update — see
[Re-running](#re-running-and-how-you-update) below.

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

## Re-running (and how you update)

**Every step runs on every invocation.** The installer doesn't try to work out whether a step is
still needed — each underlying tool already reconciles its own state and is safe to repeat, so the
installer's job is just to run them and report honestly what happened.

That means re-running `./install.sh` is how you do three different things:

- **Repair a partial install.** A tool that fails can still leave its output behind — `uv sync`
  creates `server/.venv` before it builds dependencies, `npm ci` leaves a partial `node_modules`
  if an install dies midway, and an import creates the archive DB before it reads any transcripts.
  Re-running repairs all of those, because nothing is skipped on the basis of what's on disk.
- **Pick up changes you pulled.** After `git pull`, a re-run installs new dependencies and
  rebuilds the reading room. This matters: a stale `web/dist` would otherwise keep serving you the
  *old* UI with no indication anything was out of date.
- **Confirm you're set up.** A run against an already-current checkout does no harm; the tools
  each report that there's nothing to change.

When a step fails, the installer tells you *which* step, shows the tail of that tool's output (the
*why*), and stops. Fix the cause, run it again.

> **The cost, measured:** `npm ci` reconciles by deleting and reinstalling `node_modules` from the
> lockfile, by design — so it does real work every time rather than detecting it can skip. On a
> warm npm cache that is about 2 seconds; a whole no-change re-run (`uv sync` + `npm ci` + build)
> measured ~3 seconds. A cold cache costs whatever downloading your dependencies costs.

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
