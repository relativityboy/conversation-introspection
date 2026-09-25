# The reading room

The reading room is the web UI — the "Still Water" front-end over your archive. Start it from the
TUI with `/web start`, or standalone with `uv run introspect serve`, then open
<http://127.0.0.1:8765>. One process, one port: the API and the UI are served together.

> The UI is built once by the installer (`npm run build` → `web/dist`). If you started the server
> and see "UI: not built (API only)", run `cd web && npm run build` and restart.

## Layout and routes

- The **topbar** (top) holds the search box and the project filter.
- The **sidebar** (left) lists your conversations — a flat list, or grouped by project (see
  below).
- The **reader** (right) shows one conversation, windowed so even very long sessions scroll
  smoothly.

The URLs are shareable and encode where you are:

| URL | What it shows |
|---|---|
| `/search` | The global search surface. This is also where a bare `/` or any unknown path lands. |
| `/memories` | The Memories tab — Claude Code's per-project auto-memory files, read live off disk. |
| `/s/<uuid>` | A session in the reader. |
| `/s/<uuid>/m/<record-uuid>` | A session, deep-linked to one message (arrives centred, with a one-time glow). |
| `/s/<uuid>/a/<agent-hex>` | A subagent (sub-session) transcript. |
| `/s/<uuid>/a/<agent-hex>/m/<record-uuid>` | A subagent, deep-linked to one message. |

Two query parameters ride along and survive navigation: `?filter=` (content search) and
`?projects=` (the project filter), so a link you copy reproduces exactly what you were looking at.

## The search box

The search box in the topbar, to the left of the project chips, matches **as you type** (with a
~250 ms debounce) and scopes the sidebar to three kinds of match, unified into one query:

1. a case-insensitive **session-uuid** substring,
2. the session **title** (its archive title or the one you gave it), and
3. the **message content** — full-text over everything said in the session.

When a session matches on *content only* (not its title or uuid), a one-line highlighted **snippet**
appears under the title showing where the hit is, with the matched terms marked. Your query lives
in the URL as `?filter=`, so it's shareable and a deep link restores it instantly.

## The project filter

Next to the search box, in the topbar, is a chip bar that scopes the **whole app** — the sidebar,
the global search, and the links it builds — to a chosen set of projects, reflected in the URL as
`?projects=slug1,slug2`. By default it shows a single "all projects" chip.

It's keyboard-driven, and the Escape behavior is deliberately layered so it never destroys a
selection by accident:

- **Arrow-down** opens the project list (or moves the highlight down when it's already open).
- **Typing** filters the list (case-insensitive substring; already-selected projects drop out) and
  opens it.
- **A single Escape** closes the list *only* — it never clears your typed text and never touches
  the selected chips.
- **A second, quick Escape** (within ~400 ms) does more, based on the state at the *first* press:
  - if the list was open **or** you had text typed → it clears the typed text and closes the list;
  - if the list was already closed **and** the box was empty → it clears the selected project chips,
    collapsing back to "all projects."

Selecting a project keeps the box focused so you can add several in a row; clicking away closes the
list.

## Grouping the sidebar by project

A **`by project`** toggle sits at the right of the sidebar's All / ★ Favorites row. It switches the
sidebar between two layouts and is sticky per-browser — it's remembered in local storage, not the
URL, so it doesn't travel with a shared link, but it stays put on your machine until you flip it
back.

- **Off** (the default): the flat, most-recent-first list you already know.
- **On, with no search text and ★ Favorites off:** every project, listed alphabetically, each
  collapsed to a header showing its session count. Click a project to expand it — its sessions
  load at that point, not before — and if there are more than fit, a `showing N of M` line appears
  underneath.
- **On, with a search query or ★ Favorites active:** the tree prunes itself to only the projects
  with a match, each already expanded (there's nothing to toggle while filtering), sessions show
  the same content-match snippet the flat list would, and a `showing N of M matches` line appears
  if the match set is larger than what loaded.

The All / ★ Favorites toggle and the project chip bar work identically either way — they decide
*what* is shown; `by project` only decides *how* it's arranged.

## Root sessions and subagent sessions

The sidebar shows **root sessions** by default — conversations a human actually typed in. Sessions
that were standalone agent runs (dispatched security reviews, minion implementation jobs, other
automation) are **subagent sessions**: fully archived, searchable, and openable, but filtered from
the ambient list so the sessions that ground you stay the anchor. When any exist, a quiet line
below the list says **"N subagent sessions hidden — show"** — the count is always visible, so
nothing is ever silently missing. Revealed subagent rows render quieter than root rows, and this
works the same in the flat list and `by project` mode; the choice rides the URL. A deep link into
a subagent session shows a muted **SUBAGENT SESSION** badge in the reader header so you always
know what kind of session you've landed in. (A session with no interpreted messages at all —
a rare title-only stub — counts as neither and stays visible rather than being mislabeled.)

Global search treats subagent sessions the same way: excluded by default, with an
**"include subagent sessions"** toggle when you're deliberately hunting minion work.
(Subagent *transcripts* — agent runs attached inside a root session, reached through the
subagent chips — are a different, finer-grained thing: they're governed by the `sources=`
search axis and the drill-in links, exactly as before.)

## Editable titles

Click a conversation's title in the reader to rename it inline (it's a real button, so Enter or
Space open the editor too). The box prefills with the current title.

- **Enter** commits the new title. Clicking away also commits, but only if you changed something.
- **A single Escape** cancels — your edit is discarded and the title snaps back.
- **A second, quick Escape** (within ~400 ms) goes further: it **reverts to the original archive
  title**, clearing any custom title you'd set.

A small dot next to a renamed title marks that it's been changed; hover it to see the original
archive title.

### Naming a session from inside it

With the `session-name` skill installed (`/skill install` in the TUI), type
`/session-name <name>` in any Claude Code session and that Claude sets the same title from
inside the conversation — no need to find it in the room first. If the belt hasn't captured the
session yet, the server imports it first. The skill runs `introspect session-name` in the shell
before the model even sees your message, so it takes about half a second; the model just relays
the result. Nothing starts the server for this: if it isn't running, or predates this feature,
the line you get back says so. The same command works from any terminal:
`cd server && uv run introspect session-name --session <uuid> "<name>"`.

## Session header

The reader's header holds the conversation title, a message count, and an **`actions ▾`** dropdown menu. The **category filter** (five checkboxes with preset chips) sits beside the menu, outside it — see "Choosing what you see" below.

The **actions menu** contains three controls, each with status feedback:

- **Resume** — `⟲ resume` (or `⟲ restore & resume` if the transcript was restored from archive). Clicking opens a terminal in the session's original project directory with `claude --resume <session-id>` already running. If the original directory is missing or you're not on macOS, the button shows the command as selectable text instead. Status appears inside the button: idle label, then a spinning icon while running, then a success flash (`resumed ✓` or `restored & resumed ✓`) which auto-clears after 2 seconds. If something goes wrong (missing path, terminal app not found, platform unsupported), the button enters a sticky error state with the runnable command displayed in a detail line inside the panel — this preserves what the UI knows and never swallows a recovery path.
- **`↓ .jsonl`** — Download the byte-identical transcript as a `.jsonl` file (works with right-click and save-as).
- **Archive** — Removes the session from all read paths (sidebar, search, deep links) and drops you back at the home view. There's no confirmation dialog and no separate "archived" list; archived sessions are only recoverable via CLI with `introspect unarchive <uuid>` (you need to know the uuid — it's never listed anywhere).

## Choosing what you see

The reader's header has a **category filter**: five checkboxes deciding how much of the transcript
you see, plus three preset chips — **`chat`** · **`chat+harness`** · **`all`** — that set the boxes
with one click. The five categories partition everything the archive stored — every block of every
message belongs to exactly one, so no combination can silently lose a record:

- **You — chat**: what you typed or queued (including queued prompts the harness delivered for you)
  and interruption markers — the moments that are yours even when the words arrive harness-delivered.
- **Claude — chat**: Claude's prose — replies, dispatcher chatter — and the chips that door into a
  dispatched subagent's own transcript. A resolved dispatch reads as conversation, not machinery,
  so the doorway into subagent work lives here and stays visible in the default view.
- **Claude — thinking**: thinking blocks. Preserved thinking renders its words in the quiet
  dragonfly register (an all-thinking turn reads **CLAUDE (THINKING)**); unpersisted thinking keeps
  the honest marker (◌).
- **Tool traffic**: ordinary tool calls and their results, deliberately one box — the exchange is
  the unit worth seeing together.
- **Harness/system**: skill expansions, task notifications, command output, reminders, and every
  other piece of harness prose the archive holds, honestly labeled.

Presets: **`chat`** (the default) is You — chat + Claude — chat + Claude — thinking. **`chat+harness`**
adds Harness/system. **`all`** checks every box. A message row disappears entirely when none of its
blocks is selected; at least one box is always checked. One honest nuance: a record with no content
blocks at all (bare system furniture) has nothing to select, so it only appears via the `all`
preset — which is why "show all message types" in the deep-link recovery flow switches to `all` and
is the one state guaranteed to contain every record.

Your choice rides the URL — presets as `?view=`, custom combinations as `?select=` — so a link
carries its filter with it. (The old local-storage stickiness is retired; the URL is the state.)
The raw-record inspector keeps its own independent in-modal filter.

## Message labels

Every message's eyebrow says, as precisely as the data allows, who actually produced it — not just
which role the transcript happened to file it under. **YOU** means exactly that and nothing else: it
never labels something the harness generated on your behalf.

| Label | Meaning |
|---|---|
| **YOU** (dawn) | Something you actually typed or queued. A pasted/queued prompt rescued from an attachment record shows as **SYSTEM (YOU)** instead — materially delivered by the harness, but source-accurate to your own words — and keeps the same dawn accent. |
| **CLAUDE** (dragonfly) | Claude's own reply. |
| **CLAUDE (DISPATCH)** | Claude briefing a subagent. |
| **CLAUDE (COORDINATOR)** | The dispatching orchestrator's own chatter, mid-run, on a subagent transcript. |
| **SYSTEM (…)** (mist) | Everything else. The qualifier names the actual source: a skill injection (`SYSTEM (SKILL: brainstorming)`), a tool result, a tool-injected payload, a task notification, an automation/SDK prompt, a slash-command expansion or its output, a reminder or caveat, an interruption marker, a compaction summary. **SYSTEM (UNCLASSIFIED)** is the honest floor — it means the archive holds a record shape nothing recognizes yet, rather than a guess. |

Click the speaker name to open the raw-record inspector (below) — it shows the full classification for
that record: the kind, whether it was decided by an explicit harness field (`verified`) or by the
shape of the content (`heuristic`), and the specific signal that decided it.

In your own messages (**YOU** / **SYSTEM (YOU)**), a code fence you typed chat-style — opened
mid-line, or closed at the end of a line instead of on its own — is normalized into a real code
block for display; the raw record inspector always shows the exact bytes you typed, unchanged.

## Sharing a moment

Each entry's timestamp (the `HH:MM` in the eyebrow) is a clickable link. **Click it** to copy a deep link to that specific message — the clipboard gets the full shareable URL. A transient `copied` whisper confirms the action.

You can also use **cmd/ctrl/shift/middle-click** or **right-click** for standard link behaviors — open in new tab, copy link, etc. The tooltip says "click to copy deeplink."

## The raw-record inspector

Click the **speaker name** in the message eyebrow to open the exact stored transcript line for that message:

- **Pretty-printed** JSON by default (with syntax colorizing), plus a **"raw bytes"** toggle that shows the line verbatim.
  (If a line isn't valid JSON, it falls back to raw under a "Not valid JSON" notice.)
- An **authorship line** — `authorship: skill_injection (verified — sourceToolUseID → Skill —
  superpowers:brainstorming)` — states the record's classification kind, its basis (`verified` /
  `heuristic`), and the deciding signal, unabridged. This is where the full detail lives even when
  the eyebrow's label is condensed (a stripped skill/plugin prefix, a truncated qualifier, and so on).
- **◀ / ▶** buttons — or the **Left / Right arrow keys** — step through neighbouring records without
  leaving the inspector.
- **Escape** closes it.

This is the "show me exactly what was on disk" view — the same bytes [export](export.md) hands back.

## Search

There are two search *scopes*:

- **Global search** (`/search`, reached via the "Search all conversations" tab) — searches every
  archived conversation, grouped by session.
- **Session-scoped search** — a box in the reader header that searches within the conversation
  you're reading, as a flat, rank-ordered hit list.

The in-conversation search also offers **"search selected types only"**: when toggled on, the
session-scoped search is restricted to the categories currently checked in the reader's filter —
find a phrase only in Claude's thinking, or only in your own words. (Tool traffic never appears in
text search regardless: tool payloads aren't indexed.) The global search page doesn't carry the
toggle — it always searches everything.

In both, **Enter commits the query** (runs or updates the search) — it does not jump you into a
result. You open a result by **clicking** it: a group header opens the session, and a hit snippet
deep-links to that specific message. Hits are returned best-match-first. A hit inside a **subagent**
transcript routes through the `/a/<agent-hex>/` drill-in so you land in the right sub-session.

**Searching by message id.** A query that is exactly an API message id — `msg_` followed by letters
and digits, nothing else — skips full-text search and looks the id up directly, in both scopes. You
get the records of that one API message (one API message can span several records, so several hits
are normal), ordered as captured rather than by match rank. Ids are only stored at capture from
this version on; run `introspect reparse` once to make older history findable this way.

> The keyboard "Enter or Right opens the best hit" gesture belongs to the **TUI** search, not this
> web UI — see [The TUI](tui.md#searching).

## Memories

The **Memories** tab (`/memories`) lists Claude Code's own auto-memory files — the short markdown
notes Claude keeps per project under `~/.claude/projects/<project>/memory/*.md`. The per-project
`MEMORY.md` index file itself isn't shown; the tab replaces it as the browsing surface.

Everything here is read live off disk on every visit. Nothing is captured, indexed, archived, or
exported by this feature — the byte-faithful archive is completely untouched, and the tab is
read-only: nothing in the room edits or deletes a memory file.

- A **text box** filters by name, description, and body.
- **Type chips** narrow to a memory type (`user` / `feedback` / `project` / `reference` / any other
  type present in your files / `untyped`).
- The app-level **project filter** (shared with the other tabs) scopes the list the same way it
  scopes search and the sidebar.

Each card shows the memory's type, name, and description. Click the **name** to expand the note,
rendered as markdown. A **copy chip** copies the file's absolute path — its citation handle: paste
that path into a Claude Code session and Claude reads the file directly.

A file that can't be fully read or parsed is still listed, marked `unreadable: <reason>` — nothing
is ever hidden for being odd.

Excluded projects (the owner-only exclusion ceremony — see
[`/exclude`](tui.md#slash-commands) in the TUI) never appear in this tab, indistinguishable from a
project that simply has no memories. This is the only read path in the app that lists directories
and reads file content off disk directly rather than the captured archive, and it enforces
exclusion itself rather than relying on it already being baked into stored rows.

## Archiving (and why unarchive is CLI-only)

The reader's header has a quiet **archive** button. Archiving a session removes it from every read
path — the sidebar, search, and deep links stop surfacing it — and drops you back at the archive
home. There's no confirmation dialog and no separate "archived" list: by design, **nothing in the
UI ever lists or reveals archived sessions.**

That's exactly why the UI can't **un**archive. Restoring a session is deliberately a CLI-only,
out-of-band act: you run [`introspect unarchive <uuid>`](tui.md#slash-commands) (or `/unarchive` in
the TUI) with a uuid you already know. Making "un-hide this" require knowing the identity — rather
than picking it off a list the UI shows you — is the whole point of archiving.

## Resuming a conversation

Every conversation header has a `⟲ resume` link. Clicking it opens a terminal in the
session's original project directory with `claude --resume <session-id>` already running —
whether or not Claude Code still has the transcript.

- If the live `.jsonl` is still under `~/.claude/projects/`, it is left exactly as-is.
- If Claude Code has deleted it, the label reads `⟲ restore & resume` and the archive first
  writes the byte-identical transcript back where Claude Code expects it. Your live file is
  never overwritten — restore only happens when the file is missing.
- The terminal app defaults to macOS Terminal; set `INTROSPECT_TERMINAL_APP` (e.g. `iTerm`)
  before `introspect serve` to use another.
- If `claude` isn't on your PATH, the opened terminal copies the resume command to your
  clipboard and says so — paste and run.
- If the original project directory no longer exists, or you're not on macOS, nothing is
  launched; the reader shows the exact command to run instead.
- If the transcript was hand-edited in its source file (pretty-printed, for instance), the restored
  bytes are byte-identical but may not be consumable by `claude --resume`, which expects compact JSONL.

Launching happens on the machine running `introspect serve`. That's the point on your own
Mac — but it's one more reason never to bind the server beyond 127.0.0.1.

## "Not found" states

The app never pretends. Unknown or missing things get honest, recoverable states:

- An **unknown session** → "This conversation isn't in the archive," with a link back home.
- A **missing subagent transcript** → "This subagent transcript isn't in the archive," with a link
  back to the conversation.
- A **deep link to a message that isn't in the transcript** → "message not found in this
  conversation," with a "view from the beginning" recovery (and a "show all message types" option if
  the active view mode was hiding it — see "View modes" above).
- A **raw record that's gone** → "Couldn't load this record."
- **Empty or offline** lists render calm states rather than errors: "archive offline," "Archive is
  empty — run `introspect import`," "No conversations match," "No matches for …".

## Footer status bar

The bottom of the reading room shows archive stats (last import time, session/record counts, anomaly badge) and an **import** button. When you click it, the button shows live feedback:

- Idle: "import"
- Running: a spinning icon inside the button, label reads `import…`
- Success: a brief flash of `imported ✓` (then clears back to idle)
- Already running: a neutral flash of `already running` (no checkmark — another import process is already in progress)
- Failed: a sticky `⚠ import failed` state; hover the button to see the reason in the tooltip, or click it again to retry.

<!-- SCREENSHOTS: this page is text-only for V1. If screenshots are added later they must be
     generated from the SYNTHETIC fixture archive only (build a scratch DB from server/tests
     fixtures, serve it, capture) — never from a real archive. Good spots: the sidebar with a
     content-only snippet, the project chip bar, an open raw-record inspector, and a reader in
     `chat` view. -->
