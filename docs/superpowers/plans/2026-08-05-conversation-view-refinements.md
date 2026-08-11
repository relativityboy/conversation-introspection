# Conversation View Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the six refinements in `docs/superpowers/specs/2026-08-04-conversation-view-refinements-design.md` (the binding document for every judgment call here): ActionButton primitive, header actions menu, conversation-only empty-trim, timestamp share deeplinks, speaker-name raw-record control, inspector JSON colorizing.

**Architecture:** One new dep-free primitive (`ActionButton`, a port of relativityboy/mui-action-buttons' state machine) consumed by two features; a mirrored server+client extension of the existing `chat_only` predicate (block-content emptiness); eyebrow rework inside `MessageTurn`; colorizer by reusing the existing `MarkdownProse` pipeline. No new endpoints, no schema changes.

**Tech Stack:** React 18 + react-router 7 + @tanstack/react-query + vitest/jsdom (web); FastAPI + SQLAlchemy + pytest (server). **No new npm dependencies** — the colorizer deliberately reuses `MarkdownProse` (react-markdown + rehype-highlight already in the bundle) instead of importing highlight.js directly (a transitive-dep direct-import is hoisting-fragile, and adding a manifest entry was ruled out by the spec).

## Global Constraints

- **Zero-legacy** (standing pre-release law): delete, don't deprecate. `ResumeButton.tsx`, the StatusBar `Phase` machinery, `useTriggerImport`, `useImportRun`, and the `{}` inspect button all get REMOVED, not aliased. Grep for consumers before deleting; the grep being clean is part of the task.
- **Styling:** inline `CSSProperties` + semantic classNames. New `.css` files are allowed ONLY for what inline styles can't express (pseudo-elements, keyframes, media queries, descendant rules) — the `markdown-prose.css` precedent. This plan adds exactly two: `web/src/components/action-button.css`, `web/src/components/reader/eyebrow.css`.
- **Web tests** mock `../src/api/client` via `vi.hoisted` + `vi.mock` (pattern at `web/tests/Sidebar.test.tsx:15-27`), never global fetch. jsdom has no `navigator.clipboard` — stub it per test (Task 4 shows how). Run: `cd web && npx vitest run tests/<file>`.
- **Server tests:** `cd server && uv run pytest tests/test_api_sessions.py -q`.
- **Minion economics (relativityboy, 2026-07-28 precedent):** Task 7, 8 → Haiku; Tasks 1–6 → Sonnet (Task 3 additionally gets a code reviewer — it touches dismiss machinery and deletes a component); Task 9 → orchestrator (Fable). Escalate a task's model only after a cheap attempt actually fails.
- **Commits:** one per task; the `--author` tier names the model that typed the diff — e.g. `--author="Claude (Sonnet 5) <noreply@anthropic.com>"`. Stage exactly the task's files, never `git add -A`.
- **Copy (exact strings):** menu trigger `actions ▾` · resume flashes `resumed ✓` / `restored & resumed ✓` · import flashes `imported ✓` / `already running` · error label pattern `⚠ {text} failed` · tooltips `click to copy deeplink` and `view raw record` · whisper `copied`.
- **Esc machines stay distinct** (house rule, `TitleEditor.tsx:12-24`): ActionsMenu's Escape is element-scoped (`onKeyDown` + `stopPropagation` on its wrapper), NEVER a document keydown listener.
- **Conversation-only visibility rule** (spec §4, one definition, two implementations that Tasks 5+6 pin to each other): type ∈ {user, assistant, attachment} AND ∃ block: (`text` with non-empty `text_content`) ∨ `image` ∨ unknown kind. `thinking`, `tool_use`, `tool_result`, empty `text` never count. Full (non-chat-only) mode changes NOTHING.

---

### Task 1: `ActionButton` primitive

**Files:**
- Create: `web/src/components/ActionButton.tsx`
- Create: `web/src/components/action-button.css`
- Test: `web/tests/ActionButton.test.tsx`

**Interfaces:**
- Consumes: nothing (leaf component).
- Produces: `ActionButton` with props `{ glyph: string; text: string; onClick: () => Promise<string | void>; style?: CSSProperties; className?: string; title?: string }`. Tasks 2 and 3 import it exactly like this. Phase classes on the button element: `is-pending` / `is-success` / `is-error`; glyph wrapped in `<span className="action-button-glyph">`.

- [ ] **Step 1: Write the failing tests** (`web/tests/ActionButton.test.tsx`; `vi.useFakeTimers()` in `beforeEach`, `vi.useRealTimers()` in `afterEach`)

```tsx
import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ActionButton } from '../src/components/ActionButton'

// userEvent doesn't play well with fake timers for this machine; fireEvent-style clicks via
// button.click() inside act() are what these tests need.
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { (resolve = res), (reject = rej) })
  return { promise, resolve, reject }
}

describe('ActionButton', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('success: pending (aria-busy, clicks ignored) → default flash → idle after 2s', async () => {
    const d = deferred<void>()
    const onClick = vi.fn(() => d.promise)
    render(<ActionButton glyph="⟲" text="resume" onClick={onClick} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn).toHaveAttribute('aria-busy', 'true')
    await act(async () => btn.click()) // ignored while pending
    expect(onClick).toHaveBeenCalledTimes(1)
    await act(async () => d.resolve())
    expect(btn).toHaveTextContent('resume ✓')
    expect(btn.className).toContain('is-success')
    await act(async () => vi.advanceTimersByTime(2000))
    expect(btn).toHaveTextContent('resume')
    expect(btn.className).not.toContain('is-success')
  })

  it('a resolved string overrides the flash label', async () => {
    render(<ActionButton glyph="●" text="import" onClick={async () => 'already running'} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn).toHaveTextContent('already running')
    expect(btn.className).toContain('is-success')
  })

  it('error: sticky with message in title; click 1 dismisses without re-firing; click 2 retries', async () => {
    const onClick = vi.fn().mockRejectedValueOnce(new Error('no terminal')).mockResolvedValue(undefined)
    render(<ActionButton glyph="⟲" text="resume" onClick={onClick} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    expect(btn).toHaveTextContent('⚠ resume failed')
    expect(btn).toHaveAttribute('title', 'no terminal')
    await act(async () => vi.advanceTimersByTime(5000)) // sticky, not a flash
    expect(btn).toHaveTextContent('⚠ resume failed')
    await act(async () => btn.click()) // dismiss
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(btn).toHaveTextContent('resume')
    await act(async () => btn.click()) // retry
    expect(onClick).toHaveBeenCalledTimes(2)
  })

  it('error path schedules NO flash timer, and unmount clears the success timer', async () => {
    const d1 = deferred<void>()
    const { unmount, rerender } = render(<ActionButton glyph="●" text="import" onClick={() => d1.promise} />)
    const btn = screen.getByRole('button')
    await act(async () => btn.click())
    await act(async () => d1.reject(new Error('boom')))
    expect(vi.getTimerCount()).toBe(0) // upstream bug not ported: no stray setTimeout after error
    await act(async () => btn.click()) // dismiss
    const d2 = deferred<void>()
    rerender(<ActionButton glyph="●" text="import" onClick={() => d2.promise} />)
    await act(async () => btn.click())
    await act(async () => d2.resolve())
    expect(vi.getTimerCount()).toBe(1) // flash timer armed
    unmount()
    expect(vi.getTimerCount()).toBe(0) // cleared on unmount — no post-unmount state write
  })

  it('resolving after unmount does not throw or write state', async () => {
    const d = deferred<void>()
    const { unmount } = render(<ActionButton glyph="⟲" text="resume" onClick={() => d.promise} />)
    await act(async () => screen.getByRole('button').click())
    unmount()
    await act(async () => d.resolve()) // must be a silent no-op
    expect(vi.getTimerCount()).toBe(0)
  })
})
```

- [ ] **Step 2: Run — expect FAIL** (module not found): `cd web && npx vitest run tests/ActionButton.test.tsx`

- [ ] **Step 3: Implement `web/src/components/ActionButton.tsx`**

```tsx
import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import './action-button.css'

// Dep-free port of the AIconButton state machine from relativityboy/mui-action-buttons:
// idle → pending → success-flash (2s, auto-clear) | sticky error (click 1 dismisses without
// re-firing, click 2 retries). Ported WITHOUT three upstream defects (spec §2): the error path
// scheduled a stray success-clear timer, post-await state writes weren't unmount-safe, and the
// error carried no message. Status lives INSIDE the button — no external status row.
const FLASH_MS = 2000

type Phase =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'success'; label: string }
  | { kind: 'error'; message: string }

export interface ActionButtonProps {
  /** Leading glyph (its own span so the pending spin targets it alone). */
  glyph: string
  /** Idle label; also the stem of the default flash (`{text} ✓`) and error (`⚠ {text} failed`). */
  text: string
  /** Resolved string overrides the success-flash label; a throw enters the sticky error state. */
  onClick: () => Promise<string | void>
  style?: CSSProperties
  className?: string
  title?: string
}

export function ActionButton({ glyph, text, onClick, style, className, title }: ActionButtonProps) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const mountedRef = useRef(true)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    mountedRef.current = true // StrictMode remount re-arms the flag after the cleanup below
    return () => {
      mountedRef.current = false
      if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current)
    }
  }, [])

  async function handleClick() {
    if (phase.kind === 'pending') return
    if (phase.kind === 'error') {
      setPhase({ kind: 'idle' }) // dismiss only — the next click retries
      return
    }
    if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current)
    setPhase({ kind: 'pending' })
    try {
      const label = await onClick()
      if (!mountedRef.current) return
      setPhase({ kind: 'success', label: typeof label === 'string' ? label : `${text} ✓` })
      flashTimerRef.current = setTimeout(() => {
        flashTimerRef.current = null
        setPhase({ kind: 'idle' })
      }, FLASH_MS)
    } catch (e) {
      if (!mountedRef.current) return
      setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
    }
  }

  const isError = phase.kind === 'error'
  const label =
    phase.kind === 'success' ? phase.label
    : isError ? `⚠ ${text} failed`
    : phase.kind === 'pending' ? `${text}…` // the ellipsis IS the reduced-motion pending marker
    : text
  const classes = ['action-button', 'mono', className, phase.kind !== 'idle' ? `is-${phase.kind}` : null]
    .filter(Boolean).join(' ')

  return (
    <button
      type="button"
      className={classes}
      style={style}
      onClick={handleClick}
      aria-busy={phase.kind === 'pending' || undefined}
      title={isError ? phase.message : title}
      aria-label={isError ? `${text} — failed: ${phase.message}` : undefined}
    >
      <span className="action-button-glyph">{glyph}</span> {label}
    </button>
  )
}
```

- [ ] **Step 4: Create `web/src/components/action-button.css`** (keyframes + media query — inline styles can't express these; consumers own base chrome via `style`/`className`)

```css
/* ActionButton phase styling. Base chrome (ghost pill vs bare menu item) belongs to consumers;
   only the phase states and the pending spin live here. */
.action-button.is-success { color: var(--dragonfly); transition: color 0.3s ease-in-out; }
.action-button.is-error { color: var(--ember); }
.action-button.is-pending { cursor: default; }

.action-button-glyph { display: inline-block; }

@keyframes action-button-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.action-button.is-pending .action-button-glyph {
  animation: action-button-spin 0.9s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .action-button.is-pending .action-button-glyph { animation: none; }
  /* the `…` pending label carries the signal without motion */
}
```

- [ ] **Step 5: Run — expect PASS**: `cd web && npx vitest run tests/ActionButton.test.tsx`
- [ ] **Step 6: Lint:** `cd web && npx eslint src/components/ActionButton.tsx tests/ActionButton.test.tsx`
- [ ] **Step 7: Commit**

```bash
git add web/src/components/ActionButton.tsx web/src/components/action-button.css web/tests/ActionButton.test.tsx
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: ActionButton primitive — in-button async status, dep-free AIconButton port (refinements spec §2)"
```

---

### Task 2: StatusBar import button → `ActionButton`

**Files:**
- Modify: `web/src/components/StatusBar.tsx` (delete the `Phase` machine — lines ~8-19, ~77-129 — and the center-column conditional rendering ~147-159)
- Modify: `web/src/api/hooks.ts` (DELETE `useTriggerImport`, `useImportRun`, `importRunKey` — zero-legacy; StatusBar was their only consumer, verify with `grep -rn "useTriggerImport\|useImportRun" web/src web/tests`)
- Test: `web/tests/StatusBar.test.tsx` (extend if it exists, else create with the client-mock pattern)

**Interfaces:**
- Consumes: `ActionButton` (Task 1); `triggerImport`, `fetchImportRun`, `ApiError` from `../api/client` (existing: `triggerImport(): Promise<TriggerImportOut>` where `TriggerImportOut = { run_id: number }`; `fetchImportRun(id: number): Promise<ImportRun>` where `ImportRun.status ∈ 'running' | 'ok' | ...`); `useQueryClient`.
- Produces: no API change — StatusBar keeps its `(status text | button | anomalies+MB)` grid.

- [ ] **Step 1: Write the failing tests.** Mock `../src/api/client` via `vi.hoisted` (`triggerImport`, `fetchImportRun`, plus whatever `useStatus` needs — follow `Sidebar.test.tsx`); render inside a `QueryClientProvider` built from `makeQueryClient()` and spy `queryClient.invalidateQueries`. Fake timers for the 1s poll. Cases:

```tsx
it('ok run: pending during poll, then "imported ✓" flash + status/sessions/projects invalidated', async () => {
  triggerImport.mockResolvedValue({ run_id: 7 })
  fetchImportRun.mockResolvedValueOnce({ status: 'running' }).mockResolvedValueOnce({ status: 'ok' })
  // click → aria-busy; advance 1000ms twice; expect textContent 'imported ✓';
  // expect invalidateQueries called with { queryKey: ['status'] }, ['sessions'], ['projects']
})
it('409 on trigger: neutral "already running" flash, NO invalidations', async () => {
  triggerImport.mockRejectedValue(new ApiError(409, 'conflict'))
  // click → 'already running', className contains is-success, invalidateQueries NOT called
})
it('failed run: sticky "⚠ import failed", invalidations still fire', async () => {
  triggerImport.mockResolvedValue({ run_id: 8 })
  fetchImportRun.mockResolvedValue({ status: 'error' })
  // click → advance → '⚠ import failed' + the three invalidations
})
it('poll network error: sticky error, invalidations still fire', async () => {
  triggerImport.mockResolvedValue({ run_id: 9 })
  fetchImportRun.mockRejectedValue(new Error('down'))
})
```

(Write these as full tests, not the comment sketches above — the sketches pin the assertions each must make. Check `ApiError`'s real constructor signature in `web/src/api/client.ts` and match it.)

- [ ] **Step 2: Run — expect FAIL** (current StatusBar renders `importing…` text, hides the button, and never flashes): `cd web && npx vitest run tests/StatusBar.test.tsx`

- [ ] **Step 3: Implement.** Replace the center column with:

```tsx
<span style={{ justifySelf: 'center' }}>
  <ActionButton glyph="●" text="import" onClick={runImport} style={GHOST_BTN} />
</span>
```

and the `Phase` machine with one async function inside `StatusBar`:

```tsx
const queryClient = useQueryClient()

async function runImport(): Promise<string | void> {
  let runId: number
  try {
    runId = (await triggerImport()).run_id
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return 'already running' // benign, neutral flash
    throw new Error('import failed')
  }
  try {
    for (;;) {
      const run = await fetchImportRun(runId)
      if (run.status !== 'running') {
        if (run.status === 'ok') return 'imported ✓'
        throw new Error('import failed')
      }
      await new Promise((r) => setTimeout(r, 1000))
    }
  } finally {
    // Terminal either way (ok, failed run, dead poll): a failed poll may hide a run that
    // completed server-side, so refetched truth beats the cache — same reasoning as the old
    // Phase machine's error path. 409 returns BEFORE this block: an already-running import
    // will invalidate when ITS owner finishes; this click changed nothing.
    queryClient.invalidateQueries({ queryKey: ['status'] })
    queryClient.invalidateQueries({ queryKey: ['sessions'] })
    queryClient.invalidateQueries({ queryKey: ['projects'] })
  }
}
```

Delete `Phase`, `MESSAGE_MS`, `handledRunId`, both `useEffect`s, `handleImportClick`, and the four conditional center-column spans. Then delete `useTriggerImport` / `useImportRun` / `importRunKey` from `hooks.ts` and their imports (`triggerImport`, `fetchImportRun` move to direct imports in StatusBar). Update `useProjects`' comment (`hooks.ts:126-128`) — it names "StatusBar's import-run terminal handler," which is now `runImport`'s `finally`.

- [ ] **Step 4: Run — expect PASS**, then the whole web suite (StatusBar renders on every route; nothing else may break): `cd web && npx vitest run`
- [ ] **Step 5: Lint:** `cd web && npx eslint src/components/StatusBar.tsx src/api/hooks.ts tests/StatusBar.test.tsx`
- [ ] **Step 6: Commit**

```bash
git add web/src/components/StatusBar.tsx web/src/api/hooks.ts web/tests/StatusBar.test.tsx
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: import button adopts ActionButton — Phase machine folds into one async onClick (refinements spec §2)"
```

---

### Task 3: ActionsMenu + SessionPage header

**Files:**
- Create: `web/src/components/ActionsMenu.tsx`
- Modify: `web/src/routes/SessionPage.tsx:96-130` (meta row)
- Delete: `web/src/components/ResumeButton.tsx` (zero-legacy; its `statusText` moves into ActionsMenu)
- Test: `web/tests/ActionsMenu.test.tsx`; update any test rendering SessionPage or ResumeButton (`grep -rln "ResumeButton\|resume-button" web/tests`)

**Interfaces:**
- Consumes: `ActionButton` (Task 1); `useResumeSession` (existing, `mutateAsync(uuid) → Promise<ResumeResult>` where `ResumeResult = { mode: 'launched'|'missing_cwd'|'open_failed'|'unsupported_platform'; restored: boolean; detail?; command? }` — check exact fields in `web/src/api/types.ts`); `ArchiveButton` (existing, rendered as-is inside the panel).
- Produces: `ActionsMenu` with props `{ session: SessionDetail; backSearch: string }`.

- [ ] **Step 1: Write the failing tests** (`web/tests/ActionsMenu.test.tsx`; mock client for `postResume`/`putArchive`; `MemoryRouter` wrapper because ArchiveButton uses `useNavigate`). Cases, each a real test:

1. Closed by default; trigger `actions ▾` has `aria-expanded="false"`; panel absent.
2. Click trigger → panel with three items: button text `⟲ resume` (or `⟲ restore & resume` when `on_disk: false`), link `↓ .jsonl` with `href="/api/v1/sessions/{uuid}/export.jsonl"`, button `archive`. `aria-expanded="true"`.
3. Re-click trigger closes. Escape (keyDown on a focused item) closes and returns focus to the trigger. `mousedown` on `document.body` closes; `mousedown` inside the panel does NOT.
4. **Stays open while resume pends:** `postResume` returns a never-resolving promise → click resume → panel still present, resume button `aria-busy`.
5. **Degradation detail stays readable (spec §3.1, honoring §17.3 of the 2026-07-13 spec):** `postResume` resolves `{ mode: 'missing_cwd', restored: false, detail: '/gone/dir', command: 'claude --resume abc' }` → resume button shows `⚠ resume failed`; a `.resume-detail` element inside the panel contains the full `statusText` output (`original directory missing (/gone/dir) — run: claude --resume abc`) with `user-select: text`.
6. Success: `{ mode: 'launched', restored: true }` → flash `restored & resumed ✓`.

- [ ] **Step 2: Run — expect FAIL** (module not found): `cd web && npx vitest run tests/ActionsMenu.test.tsx`

- [ ] **Step 3: Implement `ActionsMenu.tsx`.** Shape:

```tsx
export function ActionsMenu({ session, backSearch }: { session: SessionDetail; backSearch: string }) {
  const [open, setOpen] = useState(false)
  const [resumeDetail, setResumeDetail] = useState<string | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const mutation = useResumeSession()

  // Click-outside via document mousedown + containment — deliberately NOT ProjectFilterBar's
  // blur pattern: this panel must survive focus loss while a resume is pending (spec §3).
  // Listener exists only while open; removed on close/unmount. Distinct from the TitleEditor
  // and ProjectFilterBar esc machines per the house rule — Escape here is element-scoped below.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  async function runResume(): Promise<string | void> {
    setResumeDetail(null)
    const r = await mutation.mutateAsync(session.session_uuid)
    if (r.mode === 'launched') return r.restored ? 'restored & resumed ✓' : 'resumed ✓'
    setResumeDetail(statusText(r)) // the full sentence with the runnable command — §17.3: the
    throw new Error('launch degraded — see menu for the command') // room never swallows what it knows
  }

  function close() {
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}
      onKeyDown={(e) => { if (e.key === 'Escape' && open) { e.stopPropagation(); close() } }}>
      <button ref={triggerRef} type="button" className="actions-trigger mono"
        aria-expanded={open} aria-controls="session-actions"
        onClick={() => (open ? close() : setOpen(true))} style={TRIGGER_STYLE}>
        actions ▾
      </button>
      {open && (
        <div id="session-actions" className="actions-panel" style={PANEL_STYLE}>
          <ActionButton glyph="⟲" text={session.on_disk ? 'resume' : 'restore & resume'}
            onClick={runResume} style={ITEM_STYLE} />
          {resumeDetail && (
            <span className="resume-detail mono" style={DETAIL_STYLE}>{resumeDetail}</span>
          )}
          <a href={`/api/v1/sessions/${session.session_uuid}/export.jsonl`} style={LINK_ITEM_STYLE}>
            ↓ .jsonl
          </a>
          <ArchiveButton sessionUuid={session.session_uuid} backSearch={backSearch} />
        </div>
      )}
    </div>
  )
}
```

Move `statusText` (verbatim from `ResumeButton.tsx:26-42`) into this file. Styles as module-level `CSSProperties`: `TRIGGER_STYLE` mirrors `ChatOnlyToggle`'s inactive pill (`mono 11px, 1px solid var(--shore)`, radius 999, `padding '3px 10px'`, mist, transparent bg); `PANEL_STYLE` = `{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 20, minWidth: 200, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 8, padding: '10px 12px', background: 'var(--surface)', border: '1px solid var(--shore)', borderRadius: 6, boxShadow: '0 6px 20px rgba(0,0,0,.35)' }` (the ProjectFilterBar listbox surface); `ITEM_STYLE` = the old ResumeButton `BUTTON_STYLE` (bare, dragonfly, mono 11); `LINK_ITEM_STYLE` = `{ color: 'var(--dragonfly)', textDecoration: 'none', fontFamily: 'var(--mono)', fontSize: 11 }`; `DETAIL_STYLE` = `{ color: 'var(--mist)', fontSize: 11, userSelect: 'text', maxWidth: 320 }`.

In `SessionPage.tsx` replace the `ResumeButton` + `<a ↓ .jsonl>` + `ArchiveButton` elements (keep uuid span, msgs-total span with its reflow comment, `ChatOnlyToggle`) with `<ActionsMenu session={session} backSearch={backToArchiveSearch} />` placed between the msgs-total span and `ChatOnlyToggle`. Preserve the §17/§15.1 comments by moving them onto the ActionsMenu call site. Delete `ResumeButton.tsx`; `grep -rn "ResumeButton" web/src web/tests` must come back empty.

- [ ] **Step 4: Run — expect PASS**, then full suite: `cd web && npx vitest run`
- [ ] **Step 5: Lint:** `cd web && npx eslint src/components/ActionsMenu.tsx src/routes/SessionPage.tsx tests/ActionsMenu.test.tsx`
- [ ] **Step 6: Commit**

```bash
git add web/src/components/ActionsMenu.tsx web/src/routes/SessionPage.tsx web/tests/ActionsMenu.test.tsx
git rm web/src/components/ResumeButton.tsx
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: actions ▾ menu — resume/download/archive relocate, in-button resume status + selectable degradation detail (refinements spec §3)"
```

*(If other test files referenced ResumeButton, stage those updates too — same commit, they are this task's fallout.)*

---

### Task 4: Eyebrow rework — timestamp deeplink + name as raw-record control

**Files:**
- Modify: `web/src/components/reader/MessageTurn.tsx` (eyebrow region :84-118; delete `INSPECT_BUTTON_STYLE` and the `{}` button)
- Create: `web/src/components/reader/eyebrow.css` (tooltip pseudo-element + hover/focus rules — unreachable by inline styles)
- Test: `web/tests/MessageTurn.test.tsx` (existing file — reshape)

**Interfaces:**
- Consumes: `useParams` (route params are `uuid` and `agentHex` — `App.tsx:44-51`); `onInspect` prop (unchanged signature).
- Produces: eyebrow DOM contract for Task 9's walk and future tests — `button.turn-speaker` (opens inspector), `a.turn-time` (deeplink), `span.turn-copied` (whisper), tooltip via `.sw-tip[data-tip]`. The `{}` button no longer exists anywhere.

- [ ] **Step 1: Reshape the tests.** All existing `MessageTurn` tests gain a router wrapper (route context is now a real dependency of the component):

```tsx
const SESSION_UUID = 'sess-1234'
function renderTurn(ui: ReactElement, path = `/s/${SESSION_UUID}`, pattern = '/s/:uuid') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path={pattern} element={ui} /></Routes>
    </MemoryRouter>,
  )
}
```

New failing tests (stub clipboard per test: `Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })` in `beforeEach`, delete in `afterEach`):

1. `{}` is gone: `queryByText('{}')` null even with `onInspect` supplied.
2. Speaker name is a button when `onInspect` present (`getByRole('button', { name: /view raw record — CLAUDE/ })`); clicking calls `onInspect(message.record_uuid)`. Without `onInspect`, the name renders as plain text (no button role) — the unit-test/un-wired case, same conditional the `{}` had.
3. Time renders as `a.turn-time` with `href="/s/sess-1234/m/<record_uuid>"`; under the subagent pattern (`renderTurn(ui, '/s/sess-1234/a/beef42', '/s/:uuid/a/:agentHex')`) the href is `/s/sess-1234/a/beef42/m/<record_uuid>`.
4. Plain left-click: `navigator.clipboard.writeText` called with `window.location.origin + href`; default prevented (MemoryRouter location unchanged); `span.turn-copied` appears with text `copied` and is gone after `vi.advanceTimersByTime(1600)`.
5. Ctrl-click and meta-click: `writeText` NOT called (`fireEvent.click(a, { metaKey: true })`).
6. Null timestamp: no anchor, no ` · ` separator, speaker alone (existing behavior, re-pinned).

- [ ] **Step 2: Run — expect FAIL**: `cd web && npx vitest run tests/MessageTurn.test.tsx`

- [ ] **Step 3: Implement.** In `MessageTurn`:

```tsx
import { useParams } from 'react-router-dom'
import './eyebrow.css'

// route context → this row's shareable path; null outside a session route (bare unit renders)
function useEntryHref(recordUuid: string): string | null {
  const { uuid, agentHex } = useParams()
  if (!uuid) return null
  return agentHex ? `/s/${uuid}/a/${agentHex}/m/${recordUuid}` : `/s/${uuid}/m/${recordUuid}`
}
```

Component body adds `const href = useEntryHref(message.record_uuid)`, `const [copied, setCopied] = useState(false)` with a timer ref (cleared on unmount and on re-click — same pattern as ActionButton's flash timer). Click handler:

```tsx
function handleTimeClick(e: MouseEvent<HTMLAnchorElement>) {
  // Deliberate new-tab/copy-link gestures stay native (spec §5): only a plain primary click
  // copies. Middle-click/right-click never reach onClick.
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !href) return
  e.preventDefault()
  void navigator.clipboard?.writeText(window.location.origin + href).catch(() => {})
  setCopied(true)
  if (copiedTimerRef.current !== null) clearTimeout(copiedTimerRef.current)
  copiedTimerRef.current = setTimeout(() => { copiedTimerRef.current = null; setCopied(false) }, 1600)
}
```

Eyebrow JSX replaces the single template-string span (keep the outer `turn-eyebrow-row` div; with `{}` gone, `justifyContent: 'space-between'` can drop to default — nothing right-aligned remains):

```tsx
<span className="turn-eyebrow mono" style={EYEBROW_STYLE}>
  {onInspect ? (
    <button type="button" className="turn-speaker sw-tip" data-tip="view raw record"
      aria-label={`view raw record — ${SPEAKER[voice]}`}
      onClick={() => onInspect(message.record_uuid)}>
      {SPEAKER[voice]}
    </button>
  ) : (
    SPEAKER[voice]
  )}
  {time && (
    <>
      {' · '}
      {href ? (
        <a className="turn-time sw-tip" data-tip="click to copy deeplink" href={href}
          onClick={handleTimeClick}>
          {time}
        </a>
      ) : (
        time
      )}
      {copied && <span className="turn-copied">copied</span>}
    </>
  )}
</span>
```

Delete the `{}` button, `INSPECT_BUTTON_STYLE`, and the `:43-45` comment; update the `onInspect` prop docstring (it now wires the NAME). `EYEBROW_STYLE` is the existing inline object, unchanged.

- [ ] **Step 4: Create `web/src/components/reader/eyebrow.css`**

```css
/* Eyebrow controls + the shared fast tooltip (.sw-tip). CSS file because ::after content,
   :hover/:focus-visible and the reveal delay are unreachable by the inline-style convention
   (markdown-prose.css precedent). */

.turn-speaker {
  font: inherit;
  letter-spacing: inherit;
  color: inherit;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
}
.turn-speaker:hover, .turn-speaker:focus-visible { color: var(--moonpaper); text-decoration: underline; }

.turn-time { color: inherit; text-decoration: none; }
.turn-time:hover, .turn-time:focus-visible { color: var(--moonpaper); text-decoration: underline; }

.turn-copied { color: var(--dragonfly); margin-left: 6px; }

/* Fast tooltip: ~150ms reveal (native title waits ~1s). pointer-events none so it never eats
   the click it describes. */
.sw-tip { position: relative; }
.sw-tip::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  z-index: 3;
  padding: 3px 8px;
  background: var(--surface);
  border: 1px solid var(--shore);
  border-radius: 5px;
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--mist);
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.12s ease-in-out;
  transition-delay: 0s;
}
.sw-tip:hover::after, .sw-tip:focus-visible::after { opacity: 1; transition-delay: 0.15s; }
```

- [ ] **Step 5: Run — expect PASS**, then full suite (SubagentChip/ConversationView tests render MessageTurn trees — they may need the router wrapper too; that fallout is this task's): `cd web && npx vitest run`
- [ ] **Step 6: Lint:** `cd web && npx eslint src/components/reader/MessageTurn.tsx tests/MessageTurn.test.tsx`
- [ ] **Step 7: Commit**

```bash
git add web/src/components/reader/MessageTurn.tsx web/src/components/reader/eyebrow.css web/tests/MessageTurn.test.tsx
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: eyebrow rework — HH:MM copies a deeplink, speaker name opens the raw inspector, {} retired (refinements spec §5/§6)"
```

*(Stage any other test files the router-wrapper fallout touched in the same commit.)*

---

### Task 5: Server-side conversation-only trim

**Files:**
- Modify: `server/src/introspect/api/routes/sessions.py:366-399` (`_CHAT_ONLY_TYPES` region + `type_filter` construction)
- Test: `server/tests/test_api_sessions.py`

**Interfaces:**
- Consumes: `Message`, `ContentBlock` ORM models (verify column names in `server/src/introspect/` models — expected `ContentBlock.message_id`, `ContentBlock.block_kind`, `ContentBlock.text_content`; the API layer's `BlockOut` uses exactly these names); SQLAlchemy `exists`, `and_`, `or_`, `select`.
- Produces: `chat_only=1` now also excludes content-empty rows, IDENTICALLY at all four query sites (total, around-target, around ordinal, page fetch). Task 6 mirrors this rule client-side; the two test suites cross-reference each other (parity pin).

- [ ] **Step 1: Write the failing tests.** Extend the fixtures with one message per emptiness case (follow the file's existing fixture factory; each message in the SAME transcript the existing chat_only tests use): (a) assistant with only `tool_use`+`tool_result` blocks, (b) assistant with only a `thinking` block, (c) user with one `text` block whose `text_content` is `""`, (d) assistant with one block of kind `futurekind` (unknown), (e) assistant with a non-empty `text` block (control). Tests:

```python
def test_chat_only_trims_content_empty_rows(client: TestClient) -> None:
    """PARITY PIN: mirrors web/tests/chatOnly.test.ts::trim fixtures — change both together.
    Spec §4: visible iff type qualifies AND ≥1 block shows content in conversation-only mode
    (non-empty text, image, or unknown kind). tool-only / thinking-only / empty-text rows trim."""
    all_rows = client.get(f"/api/v1/transcripts/{TRANSCRIPT_ID}/messages").json()
    filtered = client.get(f"/api/v1/transcripts/{TRANSCRIPT_ID}/messages?chat_only=1").json()
    filtered_uuids = {m["record_uuid"] for m in filtered["items"]}
    assert TOOL_ONLY_UUID not in filtered_uuids
    assert THINKING_ONLY_UUID not in filtered_uuids
    assert EMPTY_TEXT_UUID not in filtered_uuids
    assert UNKNOWN_KIND_UUID in filtered_uuids
    assert CONTROL_TEXT_UUID in filtered_uuids
    # totals agree with the trimmed item set, and the unfiltered view is untouched
    assert filtered["total"] == len(filtered_uuids) if filtered["total"] <= _DEFAULT_LIMIT else True
    assert {m["record_uuid"] for m in all_rows["items"]} >= {TOOL_ONLY_UUID, THINKING_ONLY_UUID}

def test_chat_only_around_trimmed_target_is_404(client: TestClient) -> None:
    """Deep link into a trimmed row under the filter → 404 (the reader's recovery banner path);
    the same around succeeds unfiltered."""
    r = client.get(f"/api/v1/transcripts/{TRANSCRIPT_ID}/messages?chat_only=1&around={TOOL_ONLY_UUID}")
    assert r.status_code == 404
    r = client.get(f"/api/v1/transcripts/{TRANSCRIPT_ID}/messages?around={TOOL_ONLY_UUID}")
    assert r.status_code == 200
```

(Adapt constant names to the fixture factory's actual return shapes; the asserted BEHAVIOR is the contract. If the 404 arrives via `LookupError` handling, assert whatever status the existing around-404 test asserts.)

- [ ] **Step 2: Run — expect FAIL** (tool-only/thinking-only rows currently pass the type filter): `cd server && uv run pytest tests/test_api_sessions.py -q`

- [ ] **Step 3: Implement.** Replace the bare `Message.type.in_(_CHAT_ONLY_TYPES)` with:

```python
#: Kinds with dedicated renderers, used to spot UNKNOWN kinds by exclusion (an unknown kind
#: renders a visible UnknownChip client-side, so it counts as content — forward-tolerance).
_KNOWN_BLOCK_KINDS = ("text", "thinking", "tool_use", "tool_result", "image")


def _chat_only_filter() -> ColumnElement:
    """Spec §4 (2026-08-04 refinements): conversation-only shows a row only when its TYPE
    qualifies AND at least one block renders content in that mode. thinking (the ◌ glyph),
    tool_use, tool_result and empty text don't count; images and unknown kinds do. Built once
    per request and applied at all four query sites -- same discipline as the original
    type-only filter (see the note on `type_filter` below)."""
    has_content = exists(
        select(1).where(
            ContentBlock.message_id == Message.id,
            or_(
                and_(
                    ContentBlock.block_kind == "text",
                    ContentBlock.text_content.is_not(None),
                    ContentBlock.text_content != "",
                ),
                ContentBlock.block_kind == "image",
                ContentBlock.block_kind.not_in(_KNOWN_BLOCK_KINDS),
            ),
        )
    )
    return and_(Message.type.in_(_CHAT_ONLY_TYPES), has_content)
```

and in `list_messages`: `type_filter: ColumnElement = _chat_only_filter() if chat_only else true()`. Keep `_CHAT_ONLY_TYPES` and its relativityboy-ruling comment; extend the four-sites note to mention the EXISTS rides along automatically since the filter is still built once. Add the missing imports (`exists`, `or_`, `and_` from `sqlalchemy`; `ContentBlock` from the models module — match the file's existing import style).

- [ ] **Step 4: Run — expect PASS**, then the whole server suite: `cd server && uv run pytest -q`
- [ ] **Step 5: Commit**

```bash
git add server/src/introspect/api/routes/sessions.py server/tests/test_api_sessions.py
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "server: chat_only trims content-empty rows — EXISTS-over-blocks at all four query sites (refinements spec §4)"
```

---

### Task 6: Client mirror of the trim + parity pin

**Files:**
- Modify: `web/src/lib/chatOnly.ts` (`isChatOnlyVisible` + module docstring)
- Modify: `web/src/components/reader/MessageTurn.tsx:69-74` (comment only — it describes the old zero-block-attachment special case)
- Test: `web/tests/chatOnly.test.ts` (extend if present, else create)

**Interfaces:**
- Consumes: the rule pinned by Task 5. `BlockOut` fields `block_kind: string`, `text_content: string | null` (`web/src/api/types.ts`).
- Produces: `isChatOnlyVisible(message: { type: string; blocks: readonly { block_kind: string; text_content: string | null }[] }): boolean`. Existing callers (`MessageTurn.tsx:74`, `RawRecordInspector.tsx:156`) pass full `MessageOut`s and need no change — the inspector's prev/next skipping trimmed rows is INTENDED (shared predicate; spec §4).

- [ ] **Step 1: Write the failing tests** — the parity fixture table, one case per Task 5 fixture:

```ts
import { describe, expect, it } from 'vitest'
import { isChatOnlyVisible } from '../src/lib/chatOnly'

// PARITY PIN: mirrors server/tests/test_api_sessions.py::test_chat_only_trims_content_empty_rows
// — change both together. One rule, two implementations (spec §4).
const text = (s: string | null) => ({ block_kind: 'text', text_content: s })
const kind = (k: string) => ({ block_kind: k, text_content: null })

const CASES: Array<[string, { type: string; blocks: { block_kind: string; text_content: string | null }[] }, boolean]> = [
  ['assistant, tool blocks only', { type: 'assistant', blocks: [kind('tool_use'), kind('tool_result')] }, false],
  ['assistant, thinking only (◌)', { type: 'assistant', blocks: [kind('thinking')] }, false],
  ['user, empty text', { type: 'user', blocks: [text('')] }, false],
  ['user, null text', { type: 'user', blocks: [text(null)] }, false],
  ['assistant, unknown kind', { type: 'assistant', blocks: [kind('futurekind')] }, true],
  ['assistant, real text', { type: 'assistant', blocks: [text('hello')] }, true],
  ['assistant, image only', { type: 'assistant', blocks: [kind('image')] }, true],
  ['attachment, zero blocks (furniture)', { type: 'attachment', blocks: [] }, false],
  ['attachment, rescued prompt', { type: 'attachment', blocks: [text('queued words')] }, true],
  ['system, real text (type-excluded)', { type: 'system', blocks: [text('x')] }, false],
  ['assistant, zero blocks', { type: 'assistant', blocks: [] }, false],
]

describe('isChatOnlyVisible — trim rule parity', () => {
  it.each(CASES)('%s', (_name, message, visible) => {
    expect(isChatOnlyVisible(message)).toBe(visible)
  })
})
```

- [ ] **Step 2: Run — expect FAIL** (tool-only and thinking-only currently return true): `cd web && npx vitest run tests/chatOnly.test.ts`

- [ ] **Step 3: Implement** in `chatOnly.ts` (replacing the body and the attachment special case):

```ts
// Mirror of the server's known-kinds list (routes/sessions.py `_KNOWN_BLOCK_KINDS`): unknown
// kinds render a visible UnknownChip, so they count as content.
const KNOWN_BLOCK_KINDS = new Set(['text', 'thinking', 'tool_use', 'tool_result', 'image'])

interface ChatOnlyBlock {
  block_kind: string
  text_content: string | null
}

/** Spec §4 (2026-08-04 refinements): visible in conversation-only iff the TYPE qualifies AND at
 * least one block shows content there — non-empty text, an image, or an unknown kind. thinking
 * (◌), tool blocks and empty text don't count. The single source of truth shared by MessageTurn's
 * row hiding AND the raw inspector's prev/next; the server applies the SAME rule (parity-pinned
 * by tests on both sides), so counts/centering and what the reader shows can never drift. */
export function isChatOnlyVisible(message: {
  type: string
  blocks: readonly ChatOnlyBlock[]
}): boolean {
  if (!CHAT_ONLY_TYPES.has(message.type)) return false
  return message.blocks.some(blockShowsContent)
}

function blockShowsContent(block: ChatOnlyBlock): boolean {
  if (block.block_kind === 'text') return block.text_content !== null && block.text_content !== ''
  if (block.block_kind === 'image') return true
  return !KNOWN_BLOCK_KINDS.has(block.block_kind)
}
```

Update the module docstring's first paragraph (it still describes type-only + furniture-attachment filtering) and the `MessageTurn.tsx:69-74` comment (same reason). The `readonly unknown[]` in the old signature becomes the typed block shape above — TypeScript will confirm both call sites still satisfy it (`npx tsc -b web` or the vitest run's type pass).

- [ ] **Step 4: Run — expect PASS**, then full web suite (ConversationView/RawRecordInspector tests may pin the old furniture-only behavior — update any that now expect trimming): `cd web && npx vitest run`
- [ ] **Step 5: Commit**

```bash
git add web/src/lib/chatOnly.ts web/src/components/reader/MessageTurn.tsx web/tests/chatOnly.test.ts
git commit --author="Claude (Sonnet 5) <noreply@anthropic.com>" -m "web: conversation-only trims content-empty rows — client mirror + parity pin with server rule (refinements spec §4)"
```

---

### Task 7: Inspector JSON colorizing

**Files:**
- Modify: `web/src/components/reader/RawRecordInspector.tsx:304-326` (`RawContent`'s pretty path)
- Modify: `web/src/components/reader/markdown-prose.css` (scoped inspector overrides, appended)
- Test: `web/tests/RawRecordInspector.test.tsx` (extend if present, else create with mocked `fetchRawRecord`)

**Interfaces:**
- Consumes: `MarkdownProse` (existing, `{ markdown: string }` — the react-markdown + rehype-highlight pipeline whose hljs classes `markdown-prose.css` already themes).
- Produces: pretty-mode JSON renders with `.hljs-*` token spans; `raw bytes` mode and the invalid-JSON fallback stay plain `<pre>`.

- [ ] **Step 1: Write the failing test** (mock `fetchRawRecord` to resolve `'{"a": 1, "b": "two", "c": true}'`; render the inspector with a one-item `items` array; `await screen.findBy…`):

```tsx
it('pretty mode colorizes JSON tokens via the prose pipeline; raw bytes stays plain', async () => {
  // pretty (default): token spans exist inside the prose-scoped wrapper
  const wrapper = await screen.findByTestId?.('raw-json') ?? document.querySelector('.raw-record-json')
  expect(wrapper).not.toBeNull()
  expect(wrapper!.querySelector('.hljs-attr, .hljs-string, .hljs-number')).not.toBeNull()
  // toggle raw bytes: plain pre, no token spans
  await user.click(screen.getByRole('button', { name: 'raw bytes' }))
  expect(document.querySelector('.raw-record-bytes')).not.toBeNull()
  expect(document.querySelector('.raw-record-json')).toBeNull()
})
```

(Write it as a complete test with the mock plumbing; the selector assertions above are the contract. rehype-highlight runs synchronously in jsdom — no async highlight wait beyond the fetch.)

- [ ] **Step 2: Run — expect FAIL** (pretty path is a plain `<pre>`): `cd web && npx vitest run tests/RawRecordInspector.test.tsx`

- [ ] **Step 3: Implement.** In `RawContent`, replace the final pretty return:

```tsx
// NOTE(claude): the pretty JSON rides the EXISTING MarkdownProse pipeline (react-markdown +
// rehype-highlight) as a fenced block — zero new deps, and the one Still-Water hljs theme in
// markdown-prose.css applies untouched (spec §7). The 4-backtick fence cannot be escaped by
// content: JSON.stringify(…, null, 2) output lines always start with whitespace, a quote, a
// bracket/brace, a digit/minus, or t/f/n — never a backtick at line start.
return (
  <div className="raw-record-json">
    <MarkdownProse markdown={'````json\n' + pretty + '\n````'} />
  </div>
)
```

Append to `markdown-prose.css`:

```css
/* ---- raw-record inspector's pretty-JSON (rides the prose pipeline, spec §7) ---- */
/* The modal wraps long lines (horizontal scroll in a dialog is hostile) and keeps the
   inspector's quieter 12px mono — both narrower than the prose defaults above. */
.raw-record-json .markdown-prose pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}
.raw-record-json .markdown-prose code {
  font-size: 12px;
}
```

Import `MarkdownProse` in `RawRecordInspector.tsx`. `PRE_STYLE` stays — the raw-bytes and invalid-JSON paths still use it.

- [ ] **Step 4: Run — expect PASS**, then full web suite: `cd web && npx vitest run`
- [ ] **Step 5: Commit**

```bash
git add web/src/components/reader/RawRecordInspector.tsx web/src/components/reader/markdown-prose.css web/tests/RawRecordInspector.test.tsx
git commit --author="Claude (Haiku 4.5) <noreply@anthropic.com>" -m "web: inspector pretty-JSON colorized via the prose pipeline — no new deps (refinements spec §7)"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/user/reading-room.md` (routes table :18-29 — no route changes, verify only; conversation-only :99-110; raw inspector :112-123; archiving :142-152; resume :154-172)
- Modify: `docs/superpowers/specs/2026-07-13-conversation-introspection-design.md` (§14.4, §15.2 — one-line amendment pointers)

**Interfaces:** none — prose only. The 2026-08-04 refinements spec is the source; describe behavior, don't restate implementation.

- [ ] **Step 1: reading-room.md.** (a) Session header: describe the `actions ▾` menu (resume with in-button status + degradation detail line, `↓ .jsonl`, archive) replacing the three inline controls; conversation-only toggle unchanged beside it. (b) Conversation-only section: add the trim — rows whose blocks all render nothing there (tool-only, thinking-only, empty text) disappear; full mode always shows every row and block; note the "show all message types" recovery when a shared link targets a trimmed row. (c) New "Sharing a moment" subsection: click a timestamp to copy a deeplink; cmd/ctrl/middle-click opens it like any link. (d) Raw inspector section: trigger is now the speaker name (`{}` is gone); pretty view is colorized. (e) Import button in the footer: status now shows inside the button.
- [ ] **Step 2: 2026-07-13 spec pointers.** Under §14.4 append: `*(2026-08-04: conversation-only additionally trims content-empty rows — see docs/superpowers/specs/2026-08-04-conversation-view-refinements-design.md §4.)*` Under §15.2 append: `*(2026-08-04: the inspector's trigger is the speaker name, not a `{}` glyph, and pretty JSON is colorized — refinements spec §6/§7. The Phase-4 "¶ anchor" backlog item is superseded by the timestamp deeplink, refinements spec §5.)*`
- [ ] **Step 3: Verify docs build/lint if any (none exists — read-through only), then commit**

```bash
git add docs/user/reading-room.md docs/superpowers/specs/2026-07-13-conversation-introspection-design.md
git commit --author="Claude (Haiku 4.5) <noreply@anthropic.com>" -m "docs: reading room — actions menu, conversation-only trim, timestamp deeplinks, name-triggered inspector (refinements spec §8)"
```

---

### Task 9: Full suites + the walk (orchestrator)

**Files:** none planned — fixes found here are staged per-finding.

- [ ] **Step 1: Full suites + lint:** `cd server && uv run pytest -q` · `cd web && npx vitest run` · `cd web && npx eslint .` · `cd web && npx tsc -b`.
- [ ] **Step 2: Walk the room** against the production archive (dev server per `docs/dev/README.md`). Checklist:
  1. Session header: open `actions ▾`; dismiss by outside-click, Esc (focus returns to trigger), re-click. Download `.jsonl` via the menu link.
  2. Resume: click on a session whose cwd is intact — expect a terminal to appear (desktop side effect — this is the point of the walk) and the in-button flash. If any degradation occurs, verify the detail line shows the runnable command, selectable.
  3. Archive: only on a disposable/test session, then restore via CLI; skip with a written note if none exists.
  4. Conversation-only on a ghost-heavy session (one with tool-only assistant turns): header-only rows are gone; toggle off → they return; `msgs total` label unchanged (it was always the unfiltered count).
  5. Click a timestamp → paste the URL in a new tab → lands centered with the dawn glow; cmd-click opens a tab natively. Repeat once inside a subagent transcript (`/a/` path in the copied link).
  6. Click a speaker name → inspector opens, pretty JSON colorized; `raw bytes` plain; prev/next skips trimmed rows when its conversation-only toggle is on; Esc returns focus to the name.
  7. Footer: trigger an import — spinner in-button, `imported ✓` or `already running` flash.
- [ ] **Step 3: Fixes.** Minor (copy, spacing, obvious bugs): fix inline, one commit per finding, `--author="Claude (Fable 5) <noreply@anthropic.com>"`. Anything structural: write it up for relativityboy instead of executing.
- [ ] **Step 4: Report** — walk findings, suite counts, and any §9-scope items discovered, in the session's claude_notes log.

---

## Self-review (performed at write time)

- **Spec coverage:** §2→T1+T2, §3→T3, §4→T5+T6, §5+§6→T4, §7→T7, §8→T8+each task's tests, §9 honored (no popover, no message center, no ¶ glyph, no new deps).
- **Placeholders:** Task 2 Step 1 and Task 7 Step 1 intentionally pin assertions while deferring mock plumbing to the file's existing conventions — the contract (assertions + mock values) is fully specified in both.
- **Type consistency:** `ActionButton` props identical in T1/T2/T3; `isChatOnlyVisible` signature identical in T6's test and implementation; route param names (`uuid`, `agentHex`) match `App.tsx`.
- **Known judgment call:** resume degradation modes resolve (HTTP 200) but render as ActionButton ERRORS with the full sentence in a selectable `.resume-detail` line — this honors 2026-07-13 spec §17.3 ("the room never swallows what it knows") and is a deliberate refinement of refinements-spec §3.1, patched there in the same session this plan was written.
