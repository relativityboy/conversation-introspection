# The reading room

The reading room is the web UI — the "Still Water" front-end over your archive. Start it from the
TUI with `/start-web`, or standalone with `uv run introspect serve`, then open
<http://127.0.0.1:8765>. One process, one port: the API and the UI are served together.

> The UI is built once by the installer (`npm run build` → `web/dist`). If you started the server
> and see "UI: not built (API only)", run `cd web && npm run build` and restart.

## Layout and routes

- The **sidebar** (left) lists your conversations and holds the search box and the project filter.
- The **reader** (right) shows one conversation, windowed so even very long sessions scroll
  smoothly.

The URLs are shareable and encode where you are:

| URL | What it shows |
|---|---|
| `/search` | The global search surface. This is also where a bare `/` or any unknown path lands. |
| `/s/<uuid>` | A session in the reader. |
| `/s/<uuid>/m/<record-uuid>` | A session, deep-linked to one message (arrives centred, with a one-time glow). |
| `/s/<uuid>/a/<agent-hex>` | A subagent (sub-session) transcript. |
| `/s/<uuid>/a/<agent-hex>/m/<record-uuid>` | A subagent, deep-linked to one message. |

Two query parameters ride along and survive navigation: `?filter=` (sidebar search) and
`?projects=` (the project filter), so a link you copy reproduces exactly what you were looking at.

## Sidebar content search

The search box above the sidebar matches **as you type** (with a ~250 ms debounce) and scopes the
list to three kinds of match, unified into one query:

1. a case-insensitive **session-uuid** substring,
2. the session **title** (its archive title or the one you gave it), and
3. the **message content** — full-text over everything said in the session.

When a session matches on *content only* (not its title or uuid), a one-line highlighted **snippet**
appears under the title showing where the hit is, with the matched terms marked. Your query lives
in the URL as `?filter=`, so it's shareable and a deep link restores it instantly.

## The project filter

Above the sidebar is a chip bar that scopes the **whole app** — the sidebar, the global search, and
the links it builds — to a chosen set of projects, reflected in the URL as
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

## Editable titles

Click a conversation's title in the reader to rename it inline (it's a real button, so Enter or
Space open the editor too). The box prefills with the current title.

- **Enter** commits the new title. Clicking away also commits, but only if you changed something.
- **A single Escape** cancels — your edit is discarded and the title snaps back.
- **A second, quick Escape** (within ~400 ms) goes further: it **reverts to the original archive
  title**, clearing any custom title you'd set.

A small dot next to a renamed title marks that it's been changed; hover it to see the original
archive title.

## Conversation-only mode

The reader's header has a **"conversation only"** toggle. On, it strips the transcript down to the
back-and-forth and hides the machinery:

- **Kept:** what you typed, what you pasted (queued/pasted prompts show up labeled "SYSTEM (YOU)"),
  and Claude's replies (its prose and thinking).
- **Hidden:** `system` records, and the tool-call / tool-result blocks inside messages (so subagent
  chips vanish along with the tool call that spawned them).

The toggle is **sticky across sessions** — it's remembered in your browser's local storage, not in
the URL — so once you turn it on it stays on until you turn it off.

## The raw-record inspector

Every message row carries a small monospace **`{}`** button ("Inspect raw record"). It opens the
exact stored transcript line for that message:

- **Pretty-printed** JSON by default, with a **"raw bytes"** toggle that shows the line verbatim.
  (If a line isn't valid JSON, it falls back to raw under a "Not valid JSON" notice.)
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

In both, **Enter commits the query** (runs or updates the search) — it does not jump you into a
result. You open a result by **clicking** it: a group header opens the session, and a hit snippet
deep-links to that specific message. Hits are returned best-match-first. A hit inside a **subagent**
transcript routes through the `/a/<agent-hex>/` drill-in so you land in the right sub-session.

> The keyboard "Enter or Right opens the best hit" gesture belongs to the **TUI** search, not this
> web UI — see [The TUI](tui.md#searching).

## Archiving (and why unarchive is CLI-only)

The reader's header has a quiet **archive** button. Archiving a session removes it from every read
path — the sidebar, search, and deep links stop surfacing it — and drops you back at the archive
home. There's no confirmation dialog and no separate "archived" list: by design, **nothing in the
UI ever lists or reveals archived sessions.**

That's exactly why the UI can't **un**archive. Restoring a session is deliberately a CLI-only,
out-of-band act: you run [`introspect unarchive <uuid>`](tui.md#slash-commands) (or `/unarchive` in
the TUI) with a uuid you already know. Making "un-hide this" require knowing the identity — rather
than picking it off a list the UI shows you — is the whole point of archiving.

## "Not found" states

The app never pretends. Unknown or missing things get honest, recoverable states:

- An **unknown session** → "This conversation isn't in the archive," with a link back home.
- A **missing subagent transcript** → "This subagent transcript isn't in the archive," with a link
  back to the conversation.
- A **deep link to a message that isn't in the transcript** → "message not found in this
  conversation," with a "view from the beginning" recovery (and a "show all message types" option if
  conversation-only mode was hiding it).
- A **raw record that's gone** → "Couldn't load this record."
- **Empty or offline** lists render calm states rather than errors: "archive offline," "Archive is
  empty — run `introspect import`," "No conversations match," "No matches for …".

<!-- SCREENSHOTS: this page is text-only for V1. If screenshots are added later they must be
     generated from the SYNTHETIC fixture archive only (build a scratch DB from server/tests
     fixtures, serve it, capture) — never from a real archive. Good spots: the sidebar with a
     content-only snippet, the project chip bar, an open raw-record inspector, and a
     conversation-only-mode reader. -->
