import { describe, expect, it } from 'vitest'
import { archiveTitle, displayTitle, prefillTitle } from '../src/lib/titles'
import type { SessionSummary } from '../src/api/types'

function session(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_uuid: '11111111-2222-3333-4444-555555555555',
    project_slug: '-Users-x-proj',
    ai_title: null,
    custom_title: null,
    user_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 0,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
    ...over,
  }
}

// §14.3 binding, enforced identically at every render site: user_title > ai_title >
// custom_title > uuid-prefix.
describe('displayTitle', () => {
  it('prefers user_title over everything else', () => {
    expect(
      displayTitle(session({ user_title: 'Renamed', ai_title: 'AI', custom_title: 'Custom' })),
    ).toBe('Renamed')
  })

  it('falls through to ai_title when user_title is absent', () => {
    expect(displayTitle(session({ ai_title: 'AI', custom_title: 'Custom' }))).toBe('AI')
  })

  it('falls through to custom_title when user_title and ai_title are absent', () => {
    expect(displayTitle(session({ custom_title: 'Custom' }))).toBe('Custom')
  })

  it('falls through to the uuid-prefix when nothing else is set', () => {
    expect(displayTitle(session())).toBe('11111111')
  })
})

describe('archiveTitle', () => {
  it('ignores user_title entirely -- this is the pre-rename original', () => {
    expect(archiveTitle(session({ user_title: 'Renamed', ai_title: 'AI' }))).toBe('AI')
  })

  it('falls through custom_title then the uuid-prefix', () => {
    expect(archiveTitle(session({ custom_title: 'Custom' }))).toBe('Custom')
    expect(archiveTitle(session())).toBe('11111111')
  })
})

describe('prefillTitle', () => {
  it('prefills with user_title when the session was already renamed', () => {
    expect(prefillTitle(session({ user_title: 'Renamed', ai_title: 'AI' }))).toBe('Renamed')
  })

  it('prefills with the archive title (ai_title) for an unrenamed session', () => {
    expect(prefillTitle(session({ ai_title: 'AI', custom_title: 'Custom' }))).toBe('AI')
  })

  it('prefills EMPTY, never the uuid-prefix, for a session with no titles at all', () => {
    expect(prefillTitle(session())).toBe('')
  })
})
