# Conversation View Refinements — Design Spec

**Date:** 2026-08-04
**Status:** draft, awaiting owner review
**Relates to:** `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` (§9 Web UI, §14.4 conversation-only toggle, §15.2 raw-record inspector, §17 resume links); `docs/user/reading-room.md`

## 1. Overview

Four user-facing refinements to the reading room's conversation view, plus one shared primitive they justify:

- **A.** A dep-free `ActionButton` component — async action with status *inside* the button (port of the pattern in relativityboy/mui-action-buttons).
- **B.** An **actions menu** replacing the three loose action controls in the session header.
- **C.** **Conversation-only mode trims empty messages** — rows whose blocks all render nothing there.
- **D.** Each entry's **timestamp becomes a share deeplink** (copy-on-click).
- **E.** The **speaker name becomes the raw-record control**, retiring the far-right `{}` glyph.
- **F.** **JSON colorizing** in the Raw Record Inspector.

Everything here is web-UI plus one server-filter extension (§4). No schema changes. No new dependencies.

## 2. `ActionButton` primitive (A)

New component `web/src/components/ActionButton.tsx`. State machine:

```
idle → pending → success-flash (2s, auto-clear → idle)
              → error (sticky; click 1 dismisses → idle; click 2 retries)
```

**API:**
- `glyph: string` — leading glyph (e.g. `⟲`), rendered in its own span.
- `text: string` — idle label (e.g. `resume`).
- `onClick: () => Promise<string | void>` — a resolved string overrides the success-flash label (e.g. `restored & resumed ✓`, or import's benign `already running`); default flash is `{text} ✓`. A throw enters the sticky error state.
- Optional `title`, `className` pass-throughs.

**Behavior contract:**
- Pending: glyph spins (CSS keyframe; `prefers-reduced-motion` → no spin, static `…` marker), clicks ignored, `aria-busy="true"`.
- Success: dragonfly-tinted flash for 2s, then idle. Timer cleared on unmount; **no state writes after unmount, in any path** (the upstream pattern's error path schedules a stray `setSuccess(false)` and is not unmount-safe — both defects are explicitly not ported).
- Error: ember-tinted, label `⚠ {text} failed`, thrown error's message surfaced via `title` (and appended to `aria-label`). Sticky: next click returns to idle without re-firing; the click after that retries.
- Styling: ghost-button mono chrome per the existing `GHOST_BTN` convention; colors from Still Water tokens only.

**Consumers (both in this spec's scope):**
1. Resume, as an actions-menu item (§3).
2. The StatusBar import button (`web/src/components/StatusBar.tsx`): its five-state `Phase` enum, two effects, and button-hides-while-text-shows layout collapse into one async `onClick` that POSTs `/import`, polls the run to a terminal state, then invalidates the `status`, `sessions`, and `projects` queries exactly as today. HTTP 409 resolves to the string `already running` (neutral flash — not an error); a failed run or failed poll throws `import failed`. The center column keeps its position; the button simply no longer hides.

## 3. Actions menu (B)

**`SessionPage` meta row** (`SessionPage.tsx:96-130`) becomes: uuid · `{n} msgs total` · **`actions ▾`** · conversation-only toggle. The conversation-only toggle stays outside the menu — it is view state, not an action. `SubagentPage` is unchanged (it has no actions).

New component `web/src/components/ActionsMenu.tsx`:
- **Trigger:** ghost pill matching the row's chrome, `aria-expanded` + `aria-controls`. Re-click toggles closed.
- **Panel:** absolutely positioned below the trigger — `--surface` background, `--shore` hairline, shadow, `z-index` per the ProjectFilterBar listbox precedent. Vertically stacked items:
  1. **resume** — `ActionButton` (`⟲ resume` / `⟲ restore & resume` per `session.on_disk`; success flash `resumed ✓` / `restored & resumed ✓`). Replaces `ResumeButton`'s external status span. *Degradation modes* (missing cwd / open failed / unsupported platform — HTTP 200 with a runnable command) render as the sticky error state PLUS a selectable detail line inside the panel carrying the full status text with the command — the 2026-07-13 spec's §17.3 ("the room never swallows what it knows") still binds; a 2-second flash or hover-only title would swallow it. *(Amended 2026-08-05 during plan-writing.)*
  2. **`↓ .jsonl`** — remains a real `<a href>` (right-click / save-as keep working).
  3. **archive** — unchanged semantics (PUT, then navigate away); relocation only. Its error handling is explicitly not expanded here.
- **Dismiss:** click-outside (document-level `mousedown` with containment check — deliberately *not* the ProjectFilterBar blur pattern, because the panel must survive focus loss while a resume is pending), single-stage Escape (element-scoped `onKeyDown` + `stopPropagation`, kept distinct from the TitleEditor and ProjectFilterBar machines per house rule), or re-click on the trigger. Escape and trigger-close return focus to the trigger.
- **Pending interaction:** the panel stays open during a pending resume so the in-button status is visible. If the user dismisses anyway, the action completes server-side; `ActionButton`'s unmount-safety makes that harmless, and the forfeited feedback is accepted.
- **Keyboard:** native button/link semantics, Tab between items, Escape closes. This is a disclosure, not an ARIA `menu` — no roving arrow focus for three items.

## 4. Conversation-only trims empty messages (C)

**Visibility rule** (single definition, two mirrored implementations): under conversation-only, a message renders iff

> `type ∈ {user, assistant, attachment}` **and** at least one of its blocks shows content in that mode: a `text` block with non-empty content, an `image` block, or a block of *unknown* type (the `UnknownChip` stays visible — honesty about unrecognized data). `thinking` (◌), `tool_use`, `tool_result`, and empty `text` blocks do not count.

- **Server:** extend the `chat_only=1` filter (`sessions.py`, `_CHAT_ONLY_TYPES`, all four query sites) with an `EXISTS` subquery over blocks implementing the rule above — applied identically to totals, offset pagination, and `around` centering, so counts and deep-link seeding stay truthful.
- **Client:** `isChatOnlyVisible` (`web/src/lib/chatOnly.ts`) mirrors the same rule; the current zero-block-attachment special case is subsumed and removed. `MessageTurn`'s row gate is unchanged.
- **Full mode: zero behavior change.** Every row and every block renders, including header-only rows and ◌ glyphs. The honesty markers live in full mode; conversation-only is a declared-lossy reading convenience.
- **Deep link into a trimmed row** (conversation-only on): the server's `around` lookup misses and the existing recovery UI ("view from the beginning" / "show all message types") handles it. Covered by a test, not new UI.
- **Parity pin test:** one fixture set of messages (tool-only, thinking-only, empty-text, unknown-block, mixed) asserted through both the server filter and the client mirror with identical results.

## 5. Timestamp as share deeplink (D)

In the entry eyebrow (`MessageTurn.tsx`), the `HH:MM` becomes a real anchor:

- `href` = `/s/{uuid}/m/{record_uuid}` (or `/s/{uuid}/a/{agentHex}/m/{record_uuid}` on subagent pages). **No query params** — the copied link is clean; `MessageTurn` gains access to the session/agent route context (mechanism is a plan detail).
- **Plain unmodified left-click:** `preventDefault`, copy `window.location.origin + href` to the clipboard, show a transient `copied` whisper (~1.6s). **Any modified click (cmd/ctrl/shift/alt), middle-click, or right-click passes through untouched** — deliberate new-tab/copy-link behavior stays native.
- **Tooltip:** `click to copy deeplink` — a shared CSS-only tooltip (fast reveal ~150ms, mono, surface + hairline styling; no JS tooltip library). The same pattern serves §6's control.
- The visual format `SPEAKER · HH:MM` is unchanged; only the time is interactive (the `·` stays inert). Clipboard API is available in the app's localhost secure context.

## 6. Speaker name opens the raw record (E)

- The speaker label becomes a real `<button>` opening the existing `RawRecordInspector` with that record's uuid (same `inspectUuid` path the `{}` button uses today).
- The `{}` button (`MessageTurn.tsx:108-117`), its styles, and its test pins are **removed** — the name is the one affordance, ending the orphaned-far-right glyph on wide screens.
- Hover: subtle tint + underline; tooltip `view raw record` (shared pattern from §5); `aria-label` names the speaker; visible focus ring. Enter/Space work natively; the inspector's existing focus capture/restore handles return focus.
- The inspector itself (modal presentation, keyboard navigation, verbatim-bytes fetch) is unchanged except §7.

## 7. JSON colorizing in the inspector (F)

- When the inspector's content parses as JSON (the existing pretty-print path), the pretty-printed text is token-colorized using the highlight machinery already in the bundle (rehype-highlight's underlying lowlight/highlight.js, JSON grammar). The raw-bytes fallback for non-JSON lines stays plain.
- Token colors: **reuse the single existing Still-Water hljs theme** (currently scoped `.markdown-prose` in `markdown-prose.css`, which declares itself *the* highlight theme). Implementation may extend those selectors or lift the block to a shared home — one source of truth, no second palette, no new tokens beyond existing CSS vars.
- Highlighting runs once per record load (static content; record-to-record arrow navigation re-highlights per load). No new dependency.

## 8. Documentation & tests (G)

- **Docs:** update `docs/user/reading-room.md` (header actions menu, conversation-only trim wording, share links, name-as-raw-control); add amendment pointers in the 2026-07-13 spec where this file supersedes it (§15.2 trigger; the Phase-4 "¶ anchor" backlog item is superseded by §5).
- **Tests:** ActionButton unit tests (fake timers: flash auto-clear, unmount safety in success *and* error paths, sticky-error dismiss-then-retry, resolved-string flash, `aria-busy`); ActionsMenu dismiss matrix (outside click, Escape, re-click, stays-open-while-pending); MessageTurn (time-link href per page type, copy vs modified-click pass-through, name opens inspector, `{}` absent); conversation-only parity + trim fixtures (§4); server filter tests for totals/around under the trim; StatusBar migration preserves the three query invalidations and the 409-neutral path.

## 9. Out of scope / rejected

- **Raw-entry popover to the right** — rejected; the name now triggers the existing modal instead.
- **Global message/status center** — rejected for now; with status living inside buttons there are zero external-status producers left. Revisit if one appears.
- **`¶` copy-link glyph** — superseded by §5 (timestamp is the anchor).
- **Fixes to relativityboy/mui-action-buttons** — separate repo, handled outside this spec.
- **Archive confirmation/error UX, mobile-specific layout work** — unchanged beyond what the menu inherently improves.
