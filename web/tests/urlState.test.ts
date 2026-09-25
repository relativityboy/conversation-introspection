import { describe, expect, it } from 'vitest'
import {
  readProjects,
  readRestrict,
  readShowSubagents,
  readSidebarParams,
  readSubagentSessions,
  writeProjects,
  writeRestrict,
  writeShowSubagents,
  writeSidebarParams,
  writeSubagentSessions,
} from '../src/lib/urlState'

describe('readSidebarParams', () => {
  it('defaults to empty filter and fav=false when both are absent', () => {
    expect(readSidebarParams(new URLSearchParams())).toEqual({ filter: '', fav: false })
  })

  it('reads an existing filter verbatim and fav=1 as true', () => {
    const params = new URLSearchParams('filter=horizon+band&fav=1')
    expect(readSidebarParams(params)).toEqual({ filter: 'horizon band', fav: true })
  })

  it('treats any fav value other than the literal "1" as false', () => {
    expect(readSidebarParams(new URLSearchParams('fav=true')).fav).toBe(false)
    expect(readSidebarParams(new URLSearchParams('fav=0')).fav).toBe(false)
  })

  it('ignores unrelated params entirely', () => {
    expect(readSidebarParams(new URLSearchParams('q=other&project=foo'))).toEqual({
      filter: '',
      fav: false,
    })
  })

  // Zero-legacy ruling (relativityboy, ledger #4): pre-release, the old `?title=` key just dies — no
  // fallback read. A URL carrying only the retired key must read as "no filter", not silently
  // resurrect the old param under the new name.
  it('does not read the retired `title` key — a `?title=` URL yields an empty filter', () => {
    expect(readSidebarParams(new URLSearchParams('title=zzz'))).toEqual({ filter: '', fav: false })
  })
})

describe('writeSidebarParams', () => {
  it('sets filter and preserves unrelated params', () => {
    const prev = new URLSearchParams('q=other&filter=old')
    const next = writeSidebarParams(prev, { filter: 'new' })
    expect(next.get('filter')).toBe('new')
    expect(next.get('q')).toBe('other')
  })

  it('deletes filter when written as an empty string', () => {
    const prev = new URLSearchParams('filter=old&q=other')
    const next = writeSidebarParams(prev, { filter: '' })
    expect(next.has('filter')).toBe(false)
    expect(next.get('q')).toBe('other')
  })

  it('sets fav=1 when true and preserves unrelated params', () => {
    const prev = new URLSearchParams('q=other')
    const next = writeSidebarParams(prev, { fav: true })
    expect(next.get('fav')).toBe('1')
    expect(next.get('q')).toBe('other')
  })

  it('deletes fav when written as false', () => {
    const prev = new URLSearchParams('fav=1&q=other')
    const next = writeSidebarParams(prev, { fav: false })
    expect(next.has('fav')).toBe(false)
    expect(next.get('q')).toBe('other')
  })

  it('leaves a key alone entirely when it is not present in the update', () => {
    const prev = new URLSearchParams('filter=keep&fav=1')
    const next = writeSidebarParams(prev, { fav: false })
    expect(next.get('filter')).toBe('keep')
    expect(next.has('fav')).toBe(false)
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('filter=old')
    writeSidebarParams(prev, { filter: 'new' })
    expect(prev.get('filter')).toBe('old')
  })

  it('can set filter and fav together in one call', () => {
    const prev = new URLSearchParams()
    const next = writeSidebarParams(prev, { filter: 'abc', fav: true })
    expect(next.get('filter')).toBe('abc')
    expect(next.get('fav')).toBe('1')
  })
})

describe('readProjects', () => {
  it('defaults to an empty array when the param is absent', () => {
    expect(readProjects(new URLSearchParams())).toEqual([])
  })

  it('defaults to an empty array when the param is present but empty', () => {
    expect(readProjects(new URLSearchParams('projects='))).toEqual([])
  })

  it('splits a comma list into slugs', () => {
    const params = new URLSearchParams('projects=alpha,mid,zeta')
    expect(readProjects(params)).toEqual(['alpha', 'mid', 'zeta'])
  })

  it('trims whitespace and drops empty segments (a trailing/duplicated comma is not a slug)', () => {
    const params = new URLSearchParams('projects=' + encodeURIComponent(' a , b ,, c '))
    expect(readProjects(params)).toEqual(['a', 'b', 'c'])
  })

  it('ignores unrelated params entirely', () => {
    expect(readProjects(new URLSearchParams('filter=foo&fav=1'))).toEqual([])
  })
})

describe('writeProjects', () => {
  it('sets a comma-joined list and preserves unrelated params', () => {
    const prev = new URLSearchParams('filter=keep')
    const next = writeProjects(prev, ['alpha', 'mid'])
    expect(next.get('projects')).toBe('alpha,mid')
    expect(next.get('filter')).toBe('keep')
  })

  it('deletes the param when given an empty array', () => {
    const prev = new URLSearchParams('projects=alpha&filter=keep')
    const next = writeProjects(prev, [])
    expect(next.has('projects')).toBe(false)
    expect(next.get('filter')).toBe('keep')
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('projects=alpha')
    writeProjects(prev, ['beta'])
    expect(prev.get('projects')).toBe('alpha')
  })
})

describe('readRestrict / writeRestrict', () => {
  it('defaults to false when absent', () => {
    expect(readRestrict(new URLSearchParams())).toBe(false)
  })

  it('reads restrict=1 as true, anything else as false', () => {
    expect(readRestrict(new URLSearchParams('restrict=1'))).toBe(true)
    expect(readRestrict(new URLSearchParams('restrict=true'))).toBe(false)
  })

  it('writes restrict=1 when true and deletes it when false, preserving other params', () => {
    const prev = new URLSearchParams('q=other')
    const on = writeRestrict(prev, true)
    expect(on.get('restrict')).toBe('1')
    expect(on.get('q')).toBe('other')

    const off = writeRestrict(new URLSearchParams('restrict=1&q=other'), false)
    expect(off.has('restrict')).toBe(false)
    expect(off.get('q')).toBe('other')
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('restrict=1')
    writeRestrict(prev, false)
    expect(prev.get('restrict')).toBe('1')
  })
})

describe('readShowSubagents / writeShowSubagents (Task T14, sidebar reveal toggle)', () => {
  it('defaults to false when absent', () => {
    expect(readShowSubagents(new URLSearchParams())).toBe(false)
  })

  it('reads subagents=1 as true, anything else as false', () => {
    expect(readShowSubagents(new URLSearchParams('subagents=1'))).toBe(true)
    expect(readShowSubagents(new URLSearchParams('subagents=true'))).toBe(false)
  })

  it('writes subagents=1 when true and deletes it when false, preserving other params', () => {
    const prev = new URLSearchParams('q=other')
    const on = writeShowSubagents(prev, true)
    expect(on.get('subagents')).toBe('1')
    expect(on.get('q')).toBe('other')

    const off = writeShowSubagents(new URLSearchParams('subagents=1&q=other'), false)
    expect(off.has('subagents')).toBe(false)
    expect(off.get('q')).toBe('other')
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('subagents=1')
    writeShowSubagents(prev, false)
    expect(prev.get('subagents')).toBe('1')
  })

  // Distinct params (§ urlState.ts binding comment): the sidebar's own toggle never collides
  // with the global search page's independent `subagent_sessions` toggle below, even though
  // both can be present on the SAME URL (Sidebar is mounted on every route).
  it('is independent of the search page toggle', () => {
    const params = new URLSearchParams('subagent_sessions=1')
    expect(readShowSubagents(params)).toBe(false)
  })
})

describe('readSubagentSessions / writeSubagentSessions (Task T14, global search toggle)', () => {
  it('defaults to false when absent', () => {
    expect(readSubagentSessions(new URLSearchParams())).toBe(false)
  })

  it('reads subagent_sessions=1 as true, anything else as false', () => {
    expect(readSubagentSessions(new URLSearchParams('subagent_sessions=1'))).toBe(true)
    expect(readSubagentSessions(new URLSearchParams('subagent_sessions=true'))).toBe(false)
  })

  it('writes subagent_sessions=1 when true and deletes it when false, preserving other params', () => {
    const prev = new URLSearchParams('q=other')
    const on = writeSubagentSessions(prev, true)
    expect(on.get('subagent_sessions')).toBe('1')
    expect(on.get('q')).toBe('other')

    const off = writeSubagentSessions(new URLSearchParams('subagent_sessions=1&q=other'), false)
    expect(off.has('subagent_sessions')).toBe(false)
    expect(off.get('q')).toBe('other')
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('subagent_sessions=1')
    writeSubagentSessions(prev, false)
    expect(prev.get('subagent_sessions')).toBe('1')
  })

  it('is independent of the sidebar reveal toggle', () => {
    const params = new URLSearchParams('subagents=1')
    expect(readSubagentSessions(params)).toBe(false)
  })
})
