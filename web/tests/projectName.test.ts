import { describe, expect, it } from 'vitest'
import { projectDisplayName, projectLabel } from '../src/lib/projectName'

describe('projectDisplayName', () => {
  it('cuts after the last -Users- marker', () => {
    expect(projectDisplayName('-Users-donovan-projects--ai-jetwalls')).toBe(
      'donovan-projects--ai-jetwalls',
    )
  })
  it('returns the slug verbatim when no marker exists', () => {
    expect(projectDisplayName('plain-slug')).toBe('plain-slug')
  })
})

describe('projectLabel', () => {
  it('uses the last path segment of resolved_cwd when present', () => {
    expect(projectLabel('-Users-x-proj', '/Users/x/projects/@ai/jetwalls')).toBe('jetwalls')
  })
  it('falls back to the slug-tail cut when resolved_cwd is null', () => {
    expect(projectLabel('-Users-donovan-projects--ai-jetwalls', null)).toBe(
      'donovan-projects--ai-jetwalls',
    )
  })
  it('ignores a trailing slash on resolved_cwd', () => {
    expect(projectLabel('-Users-x-proj', '/Users/x/projects/@ai/jetwalls/')).toBe('jetwalls')
  })
})
