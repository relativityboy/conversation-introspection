import { describe, expect, it } from 'vitest'
import { readSidebarParams, writeSidebarParams } from '../src/lib/urlState'

describe('readSidebarParams', () => {
  it('defaults to empty title and fav=false when both are absent', () => {
    expect(readSidebarParams(new URLSearchParams())).toEqual({ title: '', fav: false })
  })

  it('reads an existing title verbatim and fav=1 as true', () => {
    const params = new URLSearchParams('title=horizon+band&fav=1')
    expect(readSidebarParams(params)).toEqual({ title: 'horizon band', fav: true })
  })

  it('treats any fav value other than the literal "1" as false', () => {
    expect(readSidebarParams(new URLSearchParams('fav=true')).fav).toBe(false)
    expect(readSidebarParams(new URLSearchParams('fav=0')).fav).toBe(false)
  })

  it('ignores unrelated params entirely', () => {
    expect(readSidebarParams(new URLSearchParams('q=other&project=foo'))).toEqual({
      title: '',
      fav: false,
    })
  })
})

describe('writeSidebarParams', () => {
  it('sets title and preserves unrelated params', () => {
    const prev = new URLSearchParams('q=other&title=old')
    const next = writeSidebarParams(prev, { title: 'new' })
    expect(next.get('title')).toBe('new')
    expect(next.get('q')).toBe('other')
  })

  it('deletes title when written as an empty string', () => {
    const prev = new URLSearchParams('title=old&q=other')
    const next = writeSidebarParams(prev, { title: '' })
    expect(next.has('title')).toBe(false)
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
    const prev = new URLSearchParams('title=keep&fav=1')
    const next = writeSidebarParams(prev, { fav: false })
    expect(next.get('title')).toBe('keep')
    expect(next.has('fav')).toBe(false)
  })

  it('does not mutate the input URLSearchParams', () => {
    const prev = new URLSearchParams('title=old')
    writeSidebarParams(prev, { title: 'new' })
    expect(prev.get('title')).toBe('old')
  })

  it('can set title and fav together in one call', () => {
    const prev = new URLSearchParams()
    const next = writeSidebarParams(prev, { title: 'abc', fav: true })
    expect(next.get('title')).toBe('abc')
    expect(next.get('fav')).toBe('1')
  })
})
