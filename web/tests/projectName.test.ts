import { describe, expect, it } from 'vitest'
import { projectDisplayName } from '../src/lib/projectName'

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
