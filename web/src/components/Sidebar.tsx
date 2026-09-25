import type { CSSProperties } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useSessions } from '../api/hooks'
import { useSidebarTree } from '../lib/sidebarTree'
import {
  readProjects,
  readShowSubagents,
  readSidebarParams,
  writeProjects,
  writeShowSubagents,
  writeSidebarParams,
} from '../lib/urlState'
import { ProjectTree } from './ProjectTree'
import { SessionListItem } from './SessionListItem'

const SKELETON_ROWS = 3

// Styling is inline, mirroring the mockup vocabulary via className hooks only — same
// convention as HorizonBand (Task 3) and SessionListItem: no new sidebar stylesheet.
const MIST_TEXT: CSSProperties = { color: 'var(--mist)', padding: '10px 6px', fontSize: 13 }

// Task T14: the reveal control — a plain text-button, quiet by design (mist, no border/fill),
// mirroring MIST_TEXT's padding so it reads as a caption on the list rather than a new chip.
const REVEAL_STYLE: CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  color: 'var(--mist)',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  padding: '8px 6px',
}

export function Sidebar() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { filter, fav } = readSidebarParams(searchParams)
  // The ONE call site (sidebarTree.ts's binding doc comment) -- treeMode/setTreeMode flow down
  // as props/closures to whatever below needs them, never a second independent hook instance.
  const [treeMode, setTreeMode] = useSidebarTree()
  // Task T14: the sidebar's subagent-session reveal toggle. Default (false) hides subagent-
  // origin sessions from this ambient view; revealing widens the request to every origin. Tree
  // mode (ProjectTree) is deliberately NOT threaded this filter -- see the T14 write-up.
  const showSubagents = readShowSubagents(searchParams)

  function setFavorite(value: boolean) {
    setSearchParams((prev) => writeSidebarParams(prev, { fav: value }), { replace: true })
  }

  function setShowSubagents(value: boolean) {
    setSearchParams((prev) => writeShowSubagents(prev, value), { replace: true })
  }

  // Read live (not memoized) so a chip add/remove — which only changes the URL, not this
  // component's own state — re-queries immediately: readProjects always returns a fresh array,
  // and react-query's key hashing is structural (JSON.stringify), so a same-valued fresh array
  // doesn't cause a spurious refetch, while an actually-changed one does (Task 9).
  const projects = readProjects(searchParams)

  const { data, isLoading, isError, isSuccess } = useSessions({
    q: filter || undefined,
    favorite: fav || undefined,
    // Only present when non-empty — an empty `projects: []` key would still hash identically to
    // omitting it, but omitting keeps the filters object (and the wire call) honest: "no filter"
    // reads as "no projects key" rather than "an empty list of projects".
    ...(projects.length > 0 ? { projects } : {}),
    // Task T14: the default ambient view hides subagent-origin sessions (root conversations plus
    // the rare title-only "empty" stubs); revealing omits `origin` entirely so the server returns
    // every origin (absent = all, per the API contract).
    ...(showSubagents ? {} : { origin: ['root', 'empty'] }),
  })

  const search = searchParams.toString()
  const hasFilter = filter.length > 0 || fav
  // Computed over the CURRENT filters WITHOUT the origin filter (server contract) — safe to read
  // regardless of `showSubagents`. Optional-chained: fixture-driven tests elsewhere in this repo
  // that mock a bare `{ items, total }` (pre-T14 shape) must not crash the reveal control's guard.
  const hiddenSubagentCount = data?.origin_counts?.subagent ?? 0

  return (
    <>
      <Link
        // Phase 4 fixwave THE IMPORTANT (half 2): "/" is a direct link, not routed through
        // App.tsx's catch-all redirect, so it must carry the project filter itself. Deliberately
        // NOT filter/fav (TabBar's established precedent: switching surfaces resets search boxes).
        to={{
          pathname: '/',
          search: writeProjects(new URLSearchParams(), readProjects(searchParams)).toString(),
        }}
        style={{
          display: 'block',
          fontFamily: 'var(--serif)',
          fontStyle: 'italic',
          fontSize: 15,
          color: 'var(--moonpaper)',
          textDecoration: 'none',
          padding: '2px 6px 14px',
          letterSpacing: '.01em',
        }}
      >
        conversation-introspection
      </Link>

      <div
        role="group"
        aria-label="List filter"
        style={{ display: 'flex', gap: 8, margin: '12px 6px 8px' }}
      >
        <button
          type="button"
          aria-pressed={!fav}
          onClick={() => setFavorite(false)}
          style={chipStyle(!fav)}
        >
          All
        </button>
        <button
          type="button"
          aria-pressed={fav}
          onClick={() => setFavorite(true)}
          style={chipStyle(fav)}
        >
          ★ Favorites
        </button>
        <button
          type="button"
          className="tree-toggle"
          aria-pressed={treeMode}
          onClick={() => setTreeMode(!treeMode)}
          style={{ ...chipStyle(treeMode), marginLeft: 'auto' }}
        >
          by project
        </button>
      </div>

      {/* A plain div, not a nested <nav> — the app shell's own <nav aria-label="Conversation
          archive"> (App.tsx) is already the landmark for this whole region; a second nested
          nav here would create an ambiguous/duplicate "navigation" landmark. */}
      {treeMode ? (
        <ProjectTree
          q={filter}
          fav={fav}
          chips={projects}
          search={search ? `?${search}` : ''}
          showSubagents={showSubagents}
        />
      ) : (
        <div className="convo-list" style={{ marginTop: 6 }}>
          {isLoading && <SkeletonRows />}
          {isError && <p style={MIST_TEXT}>archive offline</p>}
          {isSuccess && data.items.length === 0 && (
            <p style={MIST_TEXT}>
              {hasFilter ? 'No conversations match' : 'Archive is empty — run introspect import'}
            </p>
          )}
          {isSuccess &&
            data.items.map((session) => (
              <SessionListItem
                key={session.session_uuid}
                session={session}
                search={search ? `?${search}` : ''}
              />
            ))}
        </div>
      )}
      {/* Task T14/T16: the quiet reveal control — shown only when there's something to reveal
          (guards both the hidden AND revealed states), and rendered OUTSIDE the treeMode branch
          so it stays visible/functional in BOTH modes: one `?subagents=1` toggle governs
          Sidebar's own flat-list query above AND ProjectTree's independent queries (threaded via
          the `showSubagents` prop) — "the sidebar IS the main list in both modes" (owner ruling
          2026-09-24). The count comes from THIS component's own unconditional useSessions call
          (same one the by-project toggle's pre-existing tests already document as always
          running), which shares the exact q/favorite/projects filter shape either body uses. */}
      {isSuccess && hiddenSubagentCount > 0 && (
        <button type="button" onClick={() => setShowSubagents(!showSubagents)} style={REVEAL_STYLE}>
          {showSubagents
            ? 'hide subagent sessions'
            : `${hiddenSubagentCount} subagent sessions hidden — show`}
        </button>
      )}
    </>
  )
}

function chipStyle(active: boolean): CSSProperties {
  return {
    fontFamily: 'var(--sans)',
    fontSize: 12,
    color: active ? 'var(--depth)' : 'var(--mist)',
    background: active ? 'var(--dragonfly)' : 'transparent',
    border: `1px solid ${active ? 'var(--dragonfly)' : 'var(--shore)'}`,
    borderRadius: 999,
    padding: '4px 12px',
    cursor: 'pointer',
    fontWeight: active ? 600 : 400,
  }
}

// Static (no animation — Still Water is calm) placeholder rows shown only while the sessions
// query is in flight.
function SkeletonRows() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, i) => (
        <div
          key={i}
          className="skeleton-row"
          aria-hidden="true"
          style={{
            height: 60,
            borderRadius: 8,
            background: 'var(--shore)',
            opacity: 0.4,
            marginBottom: 4,
          }}
        />
      ))}
    </>
  )
}
