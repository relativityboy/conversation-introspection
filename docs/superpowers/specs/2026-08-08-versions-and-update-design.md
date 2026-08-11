# Versions and `/update` — Design Spec

**Date:** 2026-08-08
**Status:** draft, awaiting owner review
**Relates to:** `install.sh` (always-run orchestrator this feature reuses); `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` (serve/one-port architecture); `docs/user/install.md`, `docs/user/tui.md` (update story rewritten by this feature)

## 1. Overview and motivation

Field bug, first foreign-machine deployment (2026-08-08): a second machine installed
cleanly, then updated via `git pull` alone — twice — and kept serving the old reading
room. Root cause is structural, not user error: the Python side runs from source
(`uv run`), so a pull updates it; the web UI is a **built artifact** (`web/dist`) that
only changes when `npm run build` runs. `git pull` never touches it. The documented
update path (re-run `./install.sh`) exists, but nothing in the system tells you that
you haven't run it — the served UI goes silently stale.

A second, independent contributor: `index.html` is served with no `Cache-Control`
header, so a browser's heuristic cache can keep showing an old bundle even after a
fresh build (observed on the dev machine 2026-08-07, "hard-refresh first").

Approved shape (owner, 2026-08-08): make currency a first-class, visible property
instead of adding a side-channel staleness warning —

- **Versions**, curated in a root `CHANGELOG.md` (single source of truth: top entry =
  current version + user-facing changelist).
- **Version display** in the web UI and TUI, with the web bundle's version baked in at
  build time so a stale bundle reports its own stale version.
- **`/update`** in the TUI (and `introspect update` in the CLI): fetch, show what's
  new, confirm, pull + rebuild via a new `update.sh`, bounce the in-process web
  server, and offer a consent-gated TUI self-restart when server code changed.
- **Cache policy fix** so a fresh build is actually visible on next reload.

## 2. `CHANGELOG.md` — source of truth

Location: repo root. Grammar, newest-first:

```markdown
# Changelog

## 1.2.0 — 2026-08-08
- Versions: the reading room and TUI now show which version they're running.
- `/update` in the TUI checks for, describes, and applies updates.
...

## 1.1.0 — 2026-08-08
...
```

- An entry starts with `## <version> — <ISO date>` (em-dash canonical, plain hyphen
  accepted); `<version>` is three-part semver-ish (`MAJOR.MINOR.PATCH`). Bullets under a heading are that version's
  changelist, written **for users, not reviewers**. Prose before the first `## ` is
  ignored by the parser.
- The **top entry is the current version.** No git tags; no second thing to keep in
  sync.
- Release ritual: any user-visible change lands with its changelog entry in the same
  commit series. The owner performs commits and is therefore the release-cutter;
  implementation plans must include the changelog edit so it arrives staged with the
  work.
- Version bumps: minor for features, patch for fixes; major reserved for the owner's
  judgment. No enforcement tooling — the ritual is one file, kept honest by review.
- Backfill at birth: `1.0.0` (V1 as shipped), `1.1.0` (user-visible work since V1:
  authorship labels, three-state view modes, resolved-dispatch visibility,
  pretty-printed-JSONL tolerance), `1.2.0` (this feature).

Parser: `server/src/introspect/changelog.py`, dependency-free, pure.
`parse_changelog(text) -> list[Entry]` with `Entry(version, date, bullets)`;
`current_version(text) -> str`. The parser **raises** on a malformed top entry;
runtime call sites catch, report version `unknown`, and continue — a broken changelog
must never take down serving, but it must not pass silently either (logged/printed at
each surface).

## 3. Version surfaces

- **Web bundle (baked at build time):** the vite build reads `../CHANGELOG.md` and
  defines a compile-time constant (e.g. `__APP_VERSION__`). Deliberate consequence: a
  stale `dist` displays its own stale version — the artifact cannot report a currency
  it doesn't have. Dev-mode (`npm run dev`) reads the same file at config load.
- **Server:** reads `CHANGELOG.md` at process start (same repo-walk roots as
  `_resolve_ui_dist`); the admin status payload (`StatusOut`, behind the existing
  `useStatus()` 30-second poll) gains `version`.
- **StatusBar:** shows `v1.2.0` when bundle and server agree; shows both
  (`ui v1.1.0 · server v1.2.0`) when they differ. The mismatch display IS the
  staleness warning — earned as a side effect of versioning, not built as a side
  channel. No new polling.
- **TUI:** version in the startup banner and in `/status` output.
- **Packaged/API-only installs** (no repo checkout, no CHANGELOG found): version
  reports `unknown`; the StatusBar omits the version chip rather than displaying
  `unknown` to end users.

## 4. `update.sh` — the convergence layer

Sibling of `install.sh`, same orchestrator philosophy (do no work itself; shell out
and report honestly). **Promptless by design** — consent lives in the callers
(`/update`'s y/N) or in the standalone user's decision to run it.

Flow:

1. Preflight: inside a git work tree; `git` present; working tree clean (untracked
   files are fine; modified/staged tracked files abort); local branch not diverged
   from its upstream. Any failure aborts with the exact situation and a suggested
   manual action. **No stash, merge, or reset is ever performed.**
2. `git pull --ff-only` (a diverged branch thus fails loudly even if preflight raced).
3. `./install.sh --yes --skip-import` — deps, web build, no import (cron owns
   imports).
4. Report: old → new version read from `CHANGELOG.md` before and after the pull
   (`updated 1.1.0 → 1.2.0`, or `already up to date (1.2.0)`).

Failure honesty inherited from `install.sh`: print which step failed, the tail of its
output, and that re-running `update.sh` converges.

## 5. `/update` (TUI) and `introspect update` (CLI)

Shared flow, implemented once in the server package (`introspect/update.py`) with
both surfaces calling it:

1. `git fetch` on the repo's `origin`.
2. Read the remote changelog via `git show origin/<current-branch>:CHANGELOG.md`;
   compare top version against the local file.
3. **Up to date** → `up to date (v1.2.0)`. Done.
4. **Behind** → print every entry newer than the local version (the changelist),
   then confirm: `update to v1.2.0? [y/N]`.
5. On yes: run `update.sh`, streaming its output.
6. TUI only: if the in-process web server is running, bounce it (existing
   stop-web/start-web internals) so the new `dist` is picked up.
7. If the pulled range touched `server/` (`git diff --name-only old..new -- server/`
   nonempty): the running process is stale code. TUI: offer
   `restart the TUI now? [y/N]`; on yes the app exits with a restart marker and the
   `introspect tui` entrypoint's relaunch loop starts it fresh (no in-process exec
   tricks). CLI: print that long-running `tui`/`serve` processes should be
   restarted; detection of other processes is out of scope.

Failures at any step surface the command and stderr tail; nothing is retried or
resolved silently.

## 6. Cache policy

- `index.html` and every SPA-fallback response: `Cache-Control: no-cache`
  (revalidate every load; ETag/Last-Modified still make the common case a 304).
- `/assets/*` (vite content-hashed filenames): `Cache-Control: public,
  max-age=31536000, immutable`.
- Mechanism (headers on the `FileResponse`s vs. a path-scoped middleware) is the
  plan's choice; the policy above is the contract.

Without this, `/update`'s fresh build can hide behind the browser's heuristic cache —
the one way the feature could look broken on its first outing.

## 7. Error handling summary

- Malformed changelog: raise in the parser; degrade to `unknown` with a visible note
  at the TUI/CLI surfaces (the web StatusBar omits the chip instead, §3); never
  blocks serving or updating.
- Dirty/diverged repo: abort with facts and suggested action; user decides.
- Network/git failures: command + stderr tail, honest abort, re-run converges.
- Web bounce or restart declined: fine — the version mismatch display (§3) shows the
  consequence honestly until the user acts.

## 8. Testing & validation

- `changelog.py`: unit tests — well-formed, multiple entries, prose preamble,
  malformed top entry (raises), empty file.
- Version comparison / entries-newer-than-local: pure-function unit tests.
- Update flow: integration tests against a fixture git repo (temp dir + local bare
  "origin") covering up-to-date, behind (changelist content asserted), dirty tree,
  diverged branch, and server-change detection.
- `update.sh`: pytest subprocess harness following the existing
  `server/tests/test_installer.py` precedent, run against the fixture repo.
- API: `StatusOut.version` present and correct; cache-header assertions for
  `index.html`, SPA fallback, and `/assets/*`.
- Web: StatusBar renders single version when matched, both when mismatched, no chip
  when version unknown.
- TUI: restart-marker relaunch loop unit-tested at the CLI entry layer; `/update`
  confirm flow tested with a scripted runner.
- Manual walk (per house practice): on a second machine, `git pull`-only staleness
  reproduced once more, then `/update` end-to-end including the restart offer, then
  hard-refresh-free verification that the new UI appears.

## 9. Non-goals

- No auto-rebuild at serve time (would put node in the serve path and do surprise
  work).
- No git hooks (invisible machinery in the clone).
- No import inside `update.sh`/`/update` (cron owns the belt).
- No stash/merge/reset automation on dirty or diverged repos.
- No detection of *other* running processes (a `serve` on another terminal) beyond
  the printed reminder.
- No update channel/branch selection: the current branch's upstream is the only
  source.

## 10. Owner decisions log (2026-08-08)

- Source of truth: **CHANGELOG.md**, top entry = current version (over git tags, and
  over auto-derived-from-commits).
- Restart handling: **offer y/N**, exit-with-marker + entrypoint relaunch loop (over
  always-auto-restart and never-restart).
- Scope: **full feature in one go** — update.sh, changelog, version surfaces,
  `/update`, restart loop, cache fix — over shipping update.sh first (recommended
  smaller cut, declined) or update.sh alone.
- Origin of the feature: owner's proposal (versions + `/update`), merged with the
  assistant's staleness-visibility and cache-fix analysis; bundle-baked version was
  chosen so stale artifacts self-report.
