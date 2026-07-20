import type { CSSProperties } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useProjects } from '../api/hooks'
import { readProjects, writeProjects } from '../lib/urlState'

// Two Escape keydowns this close together (ref-timestamp comparison, NOT a timer) are a double-tap.
const DOUBLE_ESC_MS = 400
const LISTBOX_ID = 'project-filter-listbox'

// Pre-press snapshot the Escape machine branches on. See handleEscape for why the branch reads the
// snapshot taken at the FIRST press of a pair rather than the live state at the second press.
interface EscState {
  listOpen: boolean
  textPresent: boolean
}

// Styling is inline, mirroring the Sidebar's convention (a local style helper + className hooks;
// no per-component stylesheet). The bar's chrome (grid-area, background, border) lives in App.css.
// This is app chrome, deliberately QUIET: mist text, --surface fills, the global dragonfly
// :focus-visible ring (theme.css). Slugs render in mono because a dir_slug reads as an identifier,
// matching the .session-meta vocabulary.
const TEXT: CSSProperties = { fontFamily: 'var(--sans)', fontSize: 12, color: 'var(--mist)' }

/**
 * App-level project-filter chip bar (Phase 4, §14.2). Its single source of truth is the
 * `?projects=` URL param (read on render, written replace-not-push on every change) — this
 * component owns the URL, not the queries; threading the selection into the session/search
 * queries is Task 9.
 */
export function ProjectFilterBar() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selected = readProjects(searchParams)

  const { data: projects } = useProjects()

  // Local UI state. `boxActive` is only meaningful when nothing is selected: it distinguishes the
  // default "all projects" chip from the empty SearchBox the chip's 'x' reveals. Once anything is
  // selected the box shows regardless (a deep-linked `?projects=` lands straight in box mode).
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const [boxActive, setBoxActive] = useState(false)
  const showBox = boxActive || selected.length > 0

  const inputRef = useRef<HTMLInputElement>(null)
  // Focus is returned to the box AFTER the re-render (so the input definitely exists when we add a
  // chip): a bump to this token schedules the effect below. When a remove collapses back to the
  // all-projects chip the input is gone and the optional-chain no-ops — correct, there's nothing
  // to focus.
  const [focusToken, setFocusToken] = useState(0)
  const requestFocus = () => setFocusToken((t) => t + 1)
  useEffect(() => {
    if (focusToken > 0) inputRef.current?.focus()
  }, [focusToken])

  // The Escape machine's two refs. `lastEscAt` is the timestamp of the previous Escape;
  // `escFirst` is the pre-press snapshot taken at that press. A double-tap consumes BOTH.
  const lastEscAt = useRef(Number.NEGATIVE_INFINITY)
  const escFirst = useRef<EscState>({ listOpen: false, textPresent: false })

  // Alphabetize by dir_slug client-side, drop already-selected slugs (so selecting can't append a
  // duplicate and the list visibly shrinks), then filter by case-insensitive substring (%str%).
  const options = useMemo(() => {
    if (!projects) return []
    const needle = text.trim().toLowerCase()
    return projects
      .filter((p) => !selected.includes(p.dir_slug))
      .filter((p) => p.dir_slug.toLowerCase().includes(needle))
      .sort((a, b) => a.dir_slug.localeCompare(b.dir_slug))
  }, [projects, selected, text])

  function commitSelection(slugs: string[]) {
    setSearchParams((prev) => writeProjects(prev, slugs), { replace: true })
  }

  function selectSlug(slug: string) {
    if (!selected.includes(slug)) commitSelection([...selected, slug])
    setText('')
    setHighlight(0)
    // List stays usable (open) after a selection — spec §14.2.
    requestFocus()
  }

  function removeSlug(slug: string) {
    const next = selected.filter((s) => s !== slug)
    commitSelection(next)
    if (next.length === 0) {
      // Zero chips → revert to the "all projects" chip; the box (and its input) unmounts.
      setBoxActive(false)
    } else {
      requestFocus()
    }
  }

  function revealBox() {
    setBoxActive(true)
    requestFocus()
  }

  function handleEscape() {
    const now = Date.now()
    const isDoubleTap = now - lastEscAt.current <= DOUBLE_ESC_MS
    const snapshot: EscState = { listOpen: open, textPresent: text.length > 0 }

    if (isDoubleTap) {
      // Branch on the state captured at the FIRST press of the pair — NOT `snapshot` (the second
      // press's live state). The first press already closed the list (single-esc behavior), so by
      // now the live state is "closed"; reading it here would send an empty-text double-tap that
      // began with the list OPEN into the remove-ALL-chips branch and silently nuke the chips.
      const first = escFirst.current
      if (first.listOpen || first.textPresent) {
        setText('')
        setOpen(false)
      } else {
        commitSelection([])
        setBoxActive(false)
        setOpen(false)
      }
      // Consume the pair: a third quick Escape must start a fresh single, not re-fire.
      lastEscAt.current = Number.NEGATIVE_INFINITY
    } else {
      // Single esc: close the list ONLY. Never clears text, never touches chips — doing either
      // would make the double-tap's first branch unreachable by two real keypresses (critique F3).
      setOpen(false)
      escFirst.current = snapshot
      lastEscAt.current = now
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        if (!open) {
          setOpen(true)
          setHighlight(0)
        } else {
          setHighlight((h) => Math.min(h + 1, options.length - 1))
        }
        break
      case 'ArrowUp':
        event.preventDefault()
        if (open) setHighlight((h) => Math.max(h - 1, 0))
        break
      case 'Enter':
        event.preventDefault()
        if (open && options[highlight]) selectSlug(options[highlight].dir_slug)
        break
      case 'Escape':
        event.preventDefault()
        handleEscape()
        break
    }
  }

  return (
    <div className="project-filter-bar">
      {showBox ? (
        <>
          <div className="pfb-combo" style={{ position: 'relative' }}>
            <input
              ref={inputRef}
              role="searchbox"
              aria-label="Filter conversations by project"
              aria-expanded={open}
              aria-controls={open ? LISTBOX_ID : undefined}
              value={text}
              placeholder="Filter by project…"
              onChange={(event) => {
                setText(event.target.value)
                setOpen(true)
                setHighlight(0)
              }}
              onKeyDown={handleKeyDown}
              style={{
                width: 200,
                background: 'var(--surface)',
                border: '1px solid var(--shore)',
                color: 'var(--moonpaper)',
                fontFamily: 'var(--sans)',
                fontSize: 12,
                padding: '5px 9px',
                borderRadius: 6,
              }}
            />
            {open && (
              <ul id={LISTBOX_ID} role="listbox" aria-label="Projects" style={listboxStyle}>
                {options.length === 0 ? (
                  <li role="option" aria-disabled="true" style={{ ...TEXT, padding: '6px 10px' }}>
                    no projects
                  </li>
                ) : (
                  options.map((p, i) => (
                    <li
                      key={p.dir_slug}
                      role="option"
                      aria-selected={i === highlight}
                      // preventDefault on mousedown keeps focus in the input across the click, so
                      // the just-added chip's focus-return lands on a still-focused box.
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => selectSlug(p.dir_slug)}
                      style={optionStyle(i === highlight)}
                    >
                      {p.dir_slug}
                    </li>
                  ))
                )}
              </ul>
            )}
          </div>
          {selected.map((slug) => (
            <span key={slug} style={chipStyle}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{slug}</span>
              <button
                type="button"
                aria-label={`Remove ${slug}`}
                onClick={() => removeSlug(slug)}
                style={chipXStyle}
              >
                ×
              </button>
            </span>
          ))}
        </>
      ) : (
        <span style={chipStyle}>
          <span style={TEXT}>all projects</span>
          <button
            type="button"
            aria-label="Filter by specific projects"
            onClick={revealBox}
            style={chipXStyle}
          >
            ×
          </button>
        </span>
      )}
    </div>
  )
}

// Small rounded token — the same chip vocabulary the Sidebar's filter chips use (rounded pill,
// --shore hairline, mist text), kept visually quiet for chrome.
const chipStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  border: '1px solid var(--shore)',
  borderRadius: 999,
  padding: '3px 6px 3px 10px',
  background: 'transparent',
  color: 'var(--mist)',
  whiteSpace: 'nowrap',
}

const chipXStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 16,
  height: 16,
  padding: 0,
  border: 'none',
  borderRadius: 999,
  background: 'transparent',
  color: 'var(--mist)',
  fontSize: 14,
  lineHeight: 1,
  cursor: 'pointer',
}

const listboxStyle: CSSProperties = {
  position: 'absolute',
  top: 'calc(100% + 4px)',
  left: 0,
  zIndex: 20,
  minWidth: 220,
  maxHeight: 260,
  overflowY: 'auto',
  margin: 0,
  padding: 4,
  listStyle: 'none',
  background: 'var(--surface)',
  border: '1px solid var(--shore)',
  borderRadius: 6,
  boxShadow: '0 6px 20px rgba(0,0,0,.35)',
}

function optionStyle(active: boolean): CSSProperties {
  return {
    padding: '6px 10px',
    borderRadius: 4,
    fontFamily: 'var(--mono)',
    fontSize: 12,
    color: 'var(--moonpaper)',
    background: active ? 'var(--shore)' : 'transparent',
    cursor: 'pointer',
  }
}
