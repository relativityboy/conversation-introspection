# Updating

`git pull` alone does not update this tool — it only changes the files on disk. The web UI needs
rebuilding, the server's dependencies can drift, and a running `introspect tui` or `introspect
serve` keeps whatever code it already loaded in memory. `/update`, `introspect update`, and
`update.sh` are the three ways to do the whole job: pull, rebuild, and tell you what changed and
what to do next.

All three read the same thing to decide what "new" means: `CHANGELOG.md`'s top entry is the
current version, compared against that same file on `origin`. There are no git tags to keep in
sync — the changelog *is* the version.

## The easy way: `/update` in the TUI

Bare `/update` never changes anything — it fetches `origin`, compares versions, and if you're
behind, prints the pending changelist (each new version's heading and bullets) and stops there:

```
## 1.2.0 — 2026-08-08
- The reading room and TUI now show which version they're running...
- `/update` in the TUI (and `introspect update` in the CLI) checks for new versions...
new version 1.2.0 available -- type '/update yes' to apply
```

`/update yes` applies it: runs the same `update.sh` an `introspect update --yes` run would, streams
its output into the TUI, and — if the web server was running — stops and restarts it so the browser
gets the fresh build on the next load. If the update touched `server/` code, it prints a hint to run
`/restart`, since a running Python process can't pick up new server code on its own; `/restart`
relaunches the TUI as a fresh process (not a same-process reload) specifically so that code loads.

## The CLI twin: `introspect update`

```bash
uv run introspect update            # check + changelist, then a real [y/N] prompt if behind
uv run introspect update --yes      # or -y: apply without prompting, for scripts
```

Same check, same changelist, same `update.sh` underneath. Without `--yes` you get an interactive
`update to 1.2.0? [y/N]` prompt after the changelist; declining prints `not updating.` and exits
`0` — declining is not an error. Exit codes: `0` for up to date, updated, declined, or local-ahead;
`1` if there's a problem (a dirty tree, a diverged branch, or the update itself failing).

## `update.sh` — no TUI or CLI needed

```bash
./update.sh
```

This is what `/update yes` and `introspect update --yes` both run underneath, so it's also fine to
call directly from a shell or a script. It's **promptless by design** — running it at all is the
consent — and it does exactly two things in order: `git pull --ff-only`, then `./install.sh --yes
--skip-import` to re-converge (reinstall dependencies, rebuild the web UI). `--skip-import` is
there because an update shouldn't also trigger an archive import as a side effect.

Every path here is **fast-forward-only and never touches uncommitted work.** A dirty working tree,
a branch with no upstream, or a diverged branch (yours has commits `origin` doesn't) all stop the
update cold with an honest message instead of stashing, merging, or resetting anything for you:

- **Dirty tree:** `working tree has uncommitted changes to tracked files -- commit or stash them
  first (update never stashes)`. Commit or stash by hand, then retry.
- **No upstream:** `the current branch has no upstream -- set one (git branch
  --set-upstream-to=...) or pull manually`.
- **Local commits origin doesn't have:** if you're behind on version but your branch also has
  commits `origin` lacks, the CLI/TUI stop before touching anything with `local branch has commits
  origin doesn't -- resolve manually (update never merges)`; `update.sh` hits the same wall as a
  failed fast-forward pull. Rebase, merge, or push yourself, then retry.
- **`LOCAL_AHEAD`:** if your own `CHANGELOG.md` version doesn't appear anywhere in origin's — the
  normal state if you're hacking on this repo yourself and haven't pushed yet — `/update` and
  `introspect update` print `local checkout (X) is ahead of origin (Y) -- nothing to update; push
  or reset is your call` and exit `0`. Not an error, just nothing to pull.

## The version chip in the status bar

The reading room's status bar shows the version it's running: a single `v1.2.0` chip when the UI
you're looking at and the server serving it agree. If they don't — `ui v1.1.0 · server v1.2.0` —
that's a **stale build**: the browser (or the served `web/dist`) is older than the server's own
code, usually because a build finished without a rebuild reaching the browser yet. Run `/update` or
`./update.sh` to bring both back in sync; no chip at all means the version couldn't be determined
(an unreadable or missing `CHANGELOG.md`) rather than a real mismatch.

You shouldn't need a hard-refresh to see a fresh build land — the reading room's shell revalidates
on every load, so a plain reload after an update is enough.
