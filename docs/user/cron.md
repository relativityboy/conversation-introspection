# Keeping it running (cron)

A single manual import only protects what existed at that moment. The transcripts still on disk
keep aging toward Claude Code's ~30-day deletion, so the archive needs to run on a recurring
schedule. On macOS and Linux the simplest way is cron, and the tool manages a cron entry for you.

Why 15 minutes by default? It's the tightest interval that matters — the gap between finishing a
conversation and having it safely archived — and it sits comfortably inside the deletion window.

## The easy way: let the tool manage it

From `server/` (or via the matching `/cron …` command in the TUI):

```bash
uv run introspect cron install          # schedule an import every 15 minutes
uv run introspect cron install --every 5   # or pick your own interval (1–60 minutes)
uv run introspect cron status           # is it scheduled? show the exact line
uv run introspect cron remove           # unschedule it
```

`install` adds — or **replaces** — exactly **one** line in your user crontab, tagged with a trailing
marker so the tool can find and manage only its own entry. Every other crontab line you have is
preserved byte-for-byte. Key properties:

- It schedules the **absolute path** to the `introspect` binary directly (no `uv` or `cd`), so it
  survives cron's minimal environment.
- It appends the job's output to `~/.conversation-introspection/cron.log`.
- Running `install` again just updates the interval — it never creates a second job.
- It refuses to install a broken line: an out-of-range interval, or an `introspect` binary it can't
  locate, is an error *before* anything is written.

`--every` accepts 1–60. Under the hood, `*/N * * * *` schedules "every N minutes" for N under 60,
and 60 becomes `0 * * * *` (the top of every hour).

## The caveat: cron runs silently

Once installed, cron fires the job with **no prompt and no notification** — on macOS especially,
you get zero feedback that it ran. Don't assume; verify:

```bash
uv run introspect cron status     # confirms it's scheduled, prints the line
uv run introspect status          # check the `last run:` timestamp is recent
```

A recent `finished_at` on the `last run:` line is cron actually invoking the tool, not just a
crontab entry that silently fails.

## The manual way — and migrating to the managed line

You can hand-edit your crontab instead (for example, to run through `uv` from the repo rather than
the installed binary):

```bash
which uv        # cron has a minimal PATH; use uv's absolute path
crontab -e
```

Add a line using **absolute paths** for both `uv` and the repo:

```cron
*/15 * * * * cd /path/to/conversation-introspection/server && /path/to/uv run introspect import >> ~/.conversation-introspection/import.log 2>&1
```

### Important: a hand-edited line is NOT recognized by the managed commands

`introspect cron status` and `introspect cron install/remove` only see the tool's **own** marked
line (identified by its trailing marker comment). A crontab line you added by hand has no marker, so
`cron status` will report **not installed** even though your manual line is scheduling imports just
fine — and `cron install` would then add a *second*, managed line alongside your manual one.

If you started with a manual line and want to switch to the managed one, **remove your manual line
first** (`crontab -e`), then run `introspect cron install`. That leaves you with exactly one entry,
managed by the tool.

## Overlap is safe

You don't need to worry about a scheduled run colliding with a manual one. `import` (and `reparse`)
take an advisory lock before touching the database, so if a run is still in progress when the next
one fires, the new one exits cleanly with `status=already_running` instead of racing the first.
