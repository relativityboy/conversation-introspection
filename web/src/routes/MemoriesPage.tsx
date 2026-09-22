import type { CSSProperties } from 'react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMemories } from '../api/hooks'
import type { MemoryOut, MemoryProjectOut } from '../api/types'
import { MemoryCard } from '../components/memories/MemoryCard'
import { projectLabel } from '../lib/projectName'
import { readProjects } from '../lib/urlState'

const MIST_TEXT: CSSProperties = { color: 'var(--mist)', fontSize: 13, padding: '18px 24px' }
const WRAP_STYLE: CSSProperties = { padding: '18px 24px 40px', maxWidth: 820 }

// Layout only; the contrast treatment is the shared `.sw-input` class (theme.css), matching
// SearchPage's input.
const INPUT_STYLE: CSSProperties = {
  width: '100%',
  fontFamily: 'var(--sans)',
  fontSize: 14,
  padding: '8px 12px',
  borderRadius: 8,
}

const CHIP_ROW_STYLE: CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }

const GROUP_HEADER_STYLE: CSSProperties = {
  fontFamily: 'var(--serif)',
  fontSize: 15,
  fontWeight: 600,
  color: 'var(--moonpaper)',
  margin: '24px 0 10px',
}

const CALM_STYLE: CSSProperties = {
  color: 'var(--mist)',
  fontFamily: 'var(--serif)',
  fontSize: 16,
  marginTop: 22,
}

// user/feedback/project/reference first (only the ones actually present), then any other type
// string alphabetically, then "untyped" (null) last.
const KNOWN_TYPE_ORDER = ['user', 'feedback', 'project', 'reference']

function chipStyle(pressed: boolean): CSSProperties {
  return {
    fontFamily: 'var(--sans)',
    fontSize: 12,
    padding: '4px 10px',
    borderRadius: 999,
    border: '1px solid var(--shore)',
    background: pressed ? 'var(--shore)' : 'transparent',
    color: pressed ? 'var(--dragonfly)' : 'var(--mist)',
    cursor: 'pointer',
  }
}

// Derived from the PROJECT-filtered set only (never the text/type filters) so the chip row stays
// stable while the user types or toggles other chips -- narrowing by type must not also make
// other types' chips disappear.
function deriveTypeChips(projects: MemoryProjectOut[]): (string | null)[] {
  const present = new Set<string | null>()
  for (const project of projects) {
    for (const memory of project.memories) {
      present.add(memory.type)
    }
  }
  const known = KNOWN_TYPE_ORDER.filter((type) => present.has(type))
  const other = [...present]
    .filter((type): type is string => type !== null && !KNOWN_TYPE_ORDER.includes(type))
    .sort((a, b) => a.localeCompare(b))
  const chips: (string | null)[] = [...known, ...other]
  if (present.has(null)) chips.push(null)
  return chips
}

function matchesText(memory: MemoryOut, needle: string): boolean {
  if (!needle) return true
  const haystack = `${memory.name} ${memory.description ?? ''} ${memory.body ?? ''}`.toLowerCase()
  return haystack.includes(needle)
}

// The read-only "Memories" tab: browse/filter Claude Code's per-project auto-memory files (Task
// 1's GET /api/v1/memories, read live off disk). There is nothing here to mutate -- the text
// filter and type chips are local UI state, not URL state; only the project filter (`?projects=`)
// is shared with the rest of the app.
export function MemoriesPage() {
  const [searchParams] = useSearchParams()
  const projects = readProjects(searchParams)
  const [textFilter, setTextFilter] = useState('')
  const [selectedTypes, setSelectedTypes] = useState<Set<string | null>>(new Set())

  const query = useMemories()

  if (query.isPending) return <p style={MIST_TEXT}>…</p>
  if (query.isError) return <p style={MIST_TEXT}>archive offline</p>

  const data = query.data
  const hasAnyMemories = data.projects.some((project) => project.memories.length > 0)

  if (!hasAnyMemories) {
    return (
      <div style={WRAP_STYLE}>
        <p style={CALM_STYLE}>No memory files found</p>
      </div>
    )
  }

  const projectFiltered =
    projects.length > 0
      ? data.projects.filter((project) => projects.includes(project.dir_slug))
      : data.projects

  const typeChips = deriveTypeChips(projectFiltered)
  const needle = textFilter.trim().toLowerCase()

  const visibleProjects = projectFiltered
    .map((project) => ({
      ...project,
      memories: project.memories.filter(
        (memory) =>
          matchesText(memory, needle) &&
          (selectedTypes.size === 0 || selectedTypes.has(memory.type)),
      ),
    }))
    .filter((project) => project.memories.length > 0)

  function toggleType(type: string | null) {
    setSelectedTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  return (
    <div style={WRAP_STYLE}>
      <input
        className="sw-input"
        type="text"
        aria-label="Filter memories"
        placeholder="Filter memories"
        value={textFilter}
        onChange={(event) => setTextFilter(event.target.value)}
        style={INPUT_STYLE}
      />

      <div style={CHIP_ROW_STYLE}>
        {typeChips.map((type) => {
          const label = type ?? 'untyped'
          const pressed = selectedTypes.has(type)
          // NOTE(claude): keyed on `type` (not `label`) so a real memory type literally named
          // "untyped" can't collide with the null-type chip's display label.
          const key = type === null ? 'null' : `t:${type}`
          return (
            <button
              key={key}
              type="button"
              aria-pressed={pressed}
              onClick={() => toggleType(type)}
              style={chipStyle(pressed)}
            >
              {label}
            </button>
          )
        })}
      </div>

      {visibleProjects.length === 0 ? (
        <p style={CALM_STYLE}>No memories match</p>
      ) : (
        visibleProjects.map((project) => (
          <section key={project.dir_slug}>
            <h2 style={GROUP_HEADER_STYLE}>
              {projectLabel(project.dir_slug, project.resolved_cwd)} ({project.memories.length})
            </h2>
            {project.memories.map((memory) => (
              <MemoryCard key={memory.filename} memory={memory} />
            ))}
          </section>
        ))
      )}
    </div>
  )
}
