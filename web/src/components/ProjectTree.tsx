import type { CSSProperties } from 'react'
import { useState } from 'react'
import { useProjects, useSessions } from '../api/hooks'
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
        <p style={{ color: 'var(--mist)', fontSize: 11, padding: '2px 6px' }}>
          showing {data.items.length} of {data.total}
        </p>
      )}
    </>
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function FilteredTree(_props: ProjectTreeProps) {
  return null // Task 6 un-stubs me
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
