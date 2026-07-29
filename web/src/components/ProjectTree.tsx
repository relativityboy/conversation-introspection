import type { CSSProperties } from 'react'
import { useState } from 'react'
import { useProjects, useSessions } from '../api/hooks'
import type { SessionSummary } from '../api/types'
import { projectDisplayName } from '../lib/projectName'
import { SessionListItem } from './SessionListItem'

export interface ProjectTreeProps {
  q: string
  fav: boolean
  chips: string[]
  search: string
}

const SKELETON_ROWS = 3

// Same convention as Sidebar's MIST_TEXT (Task 3/4): inline styling, no new stylesheet.
const MIST_TEXT: CSSProperties = { color: 'var(--mist)', padding: '10px 6px', fontSize: 13 }

const ROW_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  color: 'var(--moonpaper)',
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  width: '100%',
  padding: '6px 6px',
  textAlign: 'left',
}

const GLYPH_STYLE: CSSProperties = { color: 'var(--mist)' }

const COUNT_STYLE: CSSProperties = { marginLeft: 'auto', color: 'var(--mist)', fontSize: 11 }

// Shared "showing N of M" caption style — same mono/mist values Task 5 used inline for browse
// mode's truncation line; hoisted here so filtered mode's version (below) doesn't repeat the
// literal.
const TRUNCATION_STYLE: CSSProperties = { color: 'var(--mist)', fontSize: 11, padding: '2px 6px' }

// Filtered-mode group heading — mono/mist, matching the browse-mode row's typography (ROW_STYLE/
// GLYPH_STYLE) without the interactive affordances a static heading doesn't have.
const GROUP_HEADER_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 12,
  color: 'var(--mist)',
  padding: '6px 6px',
}

export function ProjectTree({ q, fav, chips, search }: ProjectTreeProps) {
  // Manual expand state survives filtered-mode roundtrips: FilteredTree never touches it (D3).
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const toggle = (slug: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })

  if (q.length > 0 || fav) return <FilteredTree q={q} fav={fav} chips={chips} search={search} />
  return <BrowseTree chips={chips} search={search} expanded={expanded} onToggle={toggle} />
}

interface BrowseTreeProps {
  chips: string[]
  search: string
  expanded: ReadonlySet<string>
  onToggle: (slug: string) => void
}

function BrowseTree({ chips, search, expanded, onToggle }: BrowseTreeProps) {
  const { data, isLoading, isError } = useProjects()

  if (isLoading) return <SkeletonRows />
  if (isError) return <p style={MIST_TEXT}>archive offline</p>
  // Exhaustiveness guard: isLoading/isError as separate booleans don't narrow `data` for
  // TS (unlike a single discriminated `status` check) — this is the third, success case.
  if (!data) return null

  const rows = (chips.length ? data.filter((p) => chips.includes(p.dir_slug)) : data)
    .slice()
    .sort((a, b) => projectDisplayName(a.dir_slug).localeCompare(projectDisplayName(b.dir_slug)))

  return (
    <>
      {rows.map((p) => {
        const open = expanded.has(p.dir_slug)
        return (
          <div key={p.dir_slug}>
            <button
              type="button"
              aria-expanded={open}
              onClick={() => onToggle(p.dir_slug)}
              style={ROW_STYLE}
            >
              <span style={GLYPH_STYLE}>{open ? '▾' : '▸'}</span>
              {projectDisplayName(p.dir_slug)}
              <span style={COUNT_STYLE}>{p.session_count}</span>
            </button>
            {open && (
              <div style={{ paddingLeft: 14 }}>
                <ProjectChildren slug={p.dir_slug} search={search} />
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

function ProjectChildren({ slug, search }: { slug: string; search: string }) {
  const { data, isLoading, isError, refetch } = useSessions({ projects: [slug] })

  if (isLoading) return <SkeletonRows count={1} />
  if (isError) {
    return (
      <button type="button" onClick={() => refetch()} style={MIST_TEXT}>
        failed to load — retry
      </button>
    )
  }
  if (!data) return null

  return (
    <>
      {data.items.map((s) => (
        <SessionListItem key={s.session_uuid} session={s} search={search} inTree />
      ))}
      {data.total > data.items.length && (
        <p style={TRUNCATION_STYLE}>
          showing {data.items.length} of {data.total}
        </p>
      )}
    </>
  )
}

function FilteredTree({ q, fav, chips, search }: ProjectTreeProps) {
  // ONE flat query — never per-project — is the whole point of filtered mode (spec §4.5): a
  // search or ★ Favorites toggle prunes the tree to matches across ALL projects at once, not
  // project-by-project. `expanded` (browse mode's manual toggle state) is never touched here.
  const { data, isLoading, isError } = useSessions({
    q: q || undefined,
    favorite: fav || undefined,
    ...(chips.length > 0 ? { projects: chips } : {}),
  })
  if (isLoading) return <SkeletonRows />
  if (isError) return <p style={MIST_TEXT}>archive offline</p>
  // Exhaustiveness guard: isLoading/isError as separate booleans don't narrow `data` for TS
  // (unlike a single discriminated `status` check) — this is the third, success case.
  if (!data) return null
  if (data.items.length === 0) return <p style={MIST_TEXT}>No conversations match</p>

  const groups = new Map<string, SessionSummary[]>()
  for (const item of data.items) {
    const list = groups.get(item.project_slug) ?? []
    list.push(item)
    groups.set(item.project_slug, list)
  }
  const slugs = [...groups.keys()].sort((a, b) =>
    projectDisplayName(a).localeCompare(projectDisplayName(b)),
  )
  return (
    <>
      {slugs.map((slug) => (
        <div key={slug}>
          {/* Static ▾ — auto-expanded groups are not collapsible while filtering (D3); a
              disclosure that can't close would lie as a button, so this is a heading div. */}
          <div style={GROUP_HEADER_STYLE}>▾ {projectDisplayName(slug)}</div>
          <div style={{ paddingLeft: 14 }}>
            {groups.get(slug)!.map((s) => (
              <SessionListItem key={s.session_uuid} session={s} search={search} inTree />
            ))}
          </div>
        </div>
      ))}
      {data.total > data.items.length && (
        <p style={TRUNCATION_STYLE}>
          showing {data.items.length} of {data.total} matches
        </p>
      )}
    </>
  )
}

// Static (no animation — Still Water is calm) placeholder rows, mirroring Sidebar's SkeletonRows.
// Duplicated rather than shared/exported: it's 18 lines and a one-off import isn't worth it.
function SkeletonRows({ count = SKELETON_ROWS }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
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
