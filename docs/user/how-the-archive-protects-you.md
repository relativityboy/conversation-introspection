# How the archive protects you

This is the mental model. Everything else in the tool follows from it.

## The problem: a race against deletion

Claude Code writes each session as an append-only `.jsonl` file under `~/.claude/projects/`. It
also **actively deletes older sessions** — roughly 30 days of retention, observed in practice. No
warning, no export prompt. By the time you go looking for a conversation from a few weeks ago, the
file may simply be gone.

So this is not a caching layer bolted on for convenience. It's an archive built on the assumption
that **the source will disappear**. Capture has to win the race against deletion, every time.

## Capture, then interpret

The single principle the whole data model follows is **capture, then interpret.**

Raw bytes land in the archive *unconditionally*, before any parsing is attempted. Interpretation
runs alongside capture but can never block or lose it — a record the parser doesn't understand
becomes a recorded *anomaly*, not a dropped line. The bytes are safe either way.

The data model has four layers, in that spirit:

- **Identity** — `projects`, `sessions`, `transcripts`: who the data belongs to, keyed on
  envelope fields (uuid, session id, cwd) that have stayed stable across every observed CLI
  version.
- **Archive (the system of record)** — `source_files`, `raw_records`: the exact bytes of every
  ingested line, in order. This layer is the truth. [Export](export.md) is just reading it back.
- **Interpretation (rebuildable)** — `messages`, `content_blocks`, `token_usage`: raw records
  parsed through a versioned schema. Because it's *entirely derived* from the archive layer, it can
  be thrown away and rebuilt at any time (that's what `reparse` does).
- **User data** — `favorites`, custom titles, the archived-session set: never derived from source
  data, and never touched by import or reparse.

The payoff of that separation is concrete: when a Claude Code update once introduced ~17,500
drift anomalies from fields the schema hadn't seen, nothing was lost — the bytes were already on
disk. Teaching the schema those fields and running `reparse` rebuilt the affected records from the
archive and collapsed the anomaly count back to a tiny floor. Capture had already won; only
interpretation needed to catch up.

## The deletion belt: cron

A single manual import only protects what existed at that moment. The transcripts still on disk
keep aging toward deletion. So the archive needs to run on a recurring schedule — a *belt* that
keeps catching new lines before the CLI can delete them.

That belt is cron, and the tool manages it for you: one command
([`introspect cron install`](cron.md), or `/cron install` in the TUI) schedules an import every 15
minutes. Fifteen minutes is the tightest interval that matters here — it's the gap between "you
finished a conversation" and "it's safely archived," and it's far inside the ~30-day deletion
window.

## Schema versioning and the drift floor

Claude Code's transcript format isn't public or formally versioned, and every CLI update can add
or rename fields. Rather than guess, the schema is **tolerant and versioned**:

- `parse_line` never raises. Forward drift (an unknown field) is recorded as an `info` anomaly; an
  unknown record *type* is a `warn`; genuinely broken JSON is an `error`.
- Each schema generation has a name (`introspect-schema/N`), and the archive records which
  generations it has met, so you always know what interpreted your data.
- The **drift floor** is the small, steady count of anomalies that remain after the schema has
  learned a CLI version's real shape. A big pile of `info` anomalies right after a CLI upgrade is
  the drift detector doing its job — not damage. `error`-severity anomalies are the ones worth
  investigating (and worth [reporting](../../README.md#how-to-help)).

You never manage this by hand. The database migrates itself to the current schema every time it's
opened, so pulling a newer version of the repo and running it against an existing archive just
works.

## Local-only, and what's actually in the file

The archive lives at `~/.conversation-introspection/archive.db` by default — deliberately outside
any repo working tree, so `git clean` can never touch it.

Be clear-eyed about what's in that file: **everything**, verbatim, that you've ever typed into or
received from Claude Code in an archived session — including anything you pasted into a prompt. If
you ever pasted an API key, a password, or a private message, it's sitting in that database in
plain form. That's not a bug; it's a faithful copy of what Claude Code already wrote to disk.

Nothing this tool does phones home, uploads, or syncs. The archive never leaves your machine
unless you move it yourself. The one place you can *choose* to expose it is binding the web server
to a public interface — which the tool refuses to do quietly; see the
[public-bind warning](tui.md#a-warning-about-public-bind). Treat `archive.db`, and anything you
`export` out of it, with the same care you'd give a folder of your own private messages.
