# CLAUDE.md — conversation-introspection

Practices for AI agents working in this repo. User docs: `docs/user/`. Developer
guide: `docs/dev/README.md`. Design specs: `docs/superpowers/specs/`.

## Release ritual — the changelog is yours to maintain

The agent making a change is the one who writes its changelog entry. The repo owner
is the release-cutter and edits entries at review time; your job is to make sure the
entry exists, is honest, and lands with the work.

- Any **user-visible** change lands with a `CHANGELOG.md` entry in the same commit
  series. Internal-only refactors don't need one.
- The **top entry is the current version** — every surface (TUI banner, `/status`,
  the web StatusBar, `introspect update`) reports it. There are no git tags.
- Grammar: `## MAJOR.MINOR.PATCH — YYYY-MM-DD` (em-dash canonical, plain hyphen
  accepted), then `- ` bullets.
- **Every bullet is one physical line.** The parser silently drops wrapped
  continuation lines — a wrapped bullet loses text with no error.
- Bullets are written **for users** — what changed in what they can see and do —
  not commit subjects.
- Bump minor for features, patch for fixes. `server/pyproject.toml`'s `version` and
  `introspect.__version__` are package metadata, **not** the release version; never
  bump them as part of a release.
- After editing, `server/tests/test_changelog.py::test_repo_changelog_conforms`
  parses the real file — run it.

## Invariants

- **Byte-faithful archive.** What goes in comes back out identical. The export
  roundtrip test (`server/tests/test_export_roundtrip.py`) is the bar every change
  must clear.
- **Capture, then interpret.** Raw bytes land unconditionally before parsing;
  interpretation failures become recorded anomalies, never dropped or altered lines.
- **Local-only by default.** Serving binds `127.0.0.1`; any public-bind path keeps
  its mandatory no-auth warning.
- **Zero legacy.** Delete, don't deprecate. No compat aliases or shims anywhere —
  breaking changes are narrated in the changelog instead of cushioned in code.
  (Owner-ratified policy, 2026-08-09.) Rationale, so this isn't relitigated blind:
  the user base is tiny and the contributor team is one, so efficiency wins; and the
  update flow shows users the changelist and asks consent **before** applying, so
  informed choice happens at update time rather than through compat code. If either
  premise changes — real user base, outside contributors — revisit this with the
  owner.

## Working rules

- Strictly test-first (red/green TDD). Every behavior change arrives with its tests.
- Tests are fixture-driven: never read or mutate the real archive
  (`~/.conversation-introspection/`), real transcripts (`~/.claude/projects/`), or
  this repo's own git state. Build fixtures under `tmp_path`. The one deliberate
  exception is `test_repo_changelog_conforms`, which exists to lint the real
  `CHANGELOG.md`.
- Python runs via `uv run` from `server/`; web via npm from `web/`.
- `web/package-lock.json` is authored with `npx -y npm@10 install --package-lock-only`
  (and `npx -y npm@10 audit fix`) — never a bare `npm install` on newer npm: npm 11+
  prunes required-peer entries of optional platform packages that older npm records
  and validates, which breaks `npm ci` on those machines.
  `server/tests/test_web_lockfile.py` lints the committed lock's resolution closure.
- Stage with explicit paths and commit with explicit pathspecs — never `git add -A`
  and never a bare `git commit` over an index you didn't fully build: the index may
  hold someone else's staged work.
- `claude_notes/` and `claude_tasks/` are local working notes — never stage them.
