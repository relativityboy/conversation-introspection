/**
 * Session title precedence (§14.3, binding) — the ONE place the fallback chains live.
 * SessionListItem, SessionPage/TitleEditor, and GlobalSearchTab all call into here so the
 * ordering can never drift between the sidebar, the reading room, and search results.
 */

import type { SessionSummary } from '../api/types'

/** What renders EVERYWHERE a session title is shown: `user_title > ai_title > custom_title >
 * uuid-prefix`. */
export function displayTitle(session: SessionSummary): string {
  return (
    session.user_title ?? session.ai_title ?? session.custom_title ?? session.session_uuid.slice(0, 8)
  )
}

/** The archive-derived title, ignoring any user rename -- what the session displayed before a
 * user_title existed, and what it reverts to when the user title is cleared. Used for the
 * "edited" dot's tooltip. */
export function archiveTitle(session: SessionSummary): string {
  return session.ai_title ?? session.custom_title ?? session.session_uuid.slice(0, 8)
}

/** TitleEditor's prefill value: the current user_title if the session was already renamed,
 * else the archive title's real (non-synthetic) components -- but NEVER the uuid-prefix stand-
 * in. A title-less session's edit starts from '', not from the uuid (§14.3 binding). */
export function prefillTitle(session: SessionSummary): string {
  return session.user_title ?? session.ai_title ?? session.custom_title ?? ''
}
