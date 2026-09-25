/**
 * TS mirrors of the server's response shapes.
 *
 * Field names/optionality are read from the Pydantic source, not memory — see the section
 * comments below for exactly which Python file/class each type mirrors. Datetimes serialize
 * to ISO-8601 strings (Pydantic v2's default `datetime` JSON encoding), typed here as
 * `string` (never/null per the source field's optionality).
 */

// --- server/src/introspect/api/models.py -------------------------------------------------

// Task T14: a session's ORIGIN — whether it's a root conversation (the user's own grounding
// point), a standalone subagent-run session (no root conversation of its own — a Task-tool
// dispatch that got captured as its own top-level session file), or "empty" (a title-only stub
// with no captured content, present today but never previously classified). `GET /sessions`'s
// `origin=` query param takes a CSV of these three literals.
export type SessionOrigin = 'root' | 'subagent' | 'empty'

export interface SessionSummary {
  session_uuid: string
  project_slug: string
  ai_title: string | null
  custom_title: string | null
  user_title: string | null
  started_at: string | null
  last_activity_at: string | null
  message_count: number
  favorite: boolean
  // Populated by GET /sessions?q= ONLY for rows matched by conversational content and NOT by
  // uuid/title (a <mark>-wrapped best snippet); null on unfiltered lists, detail, and title/
  // uuid matches. See server routes/sessions.py `list_sessions` for the match-attribution rule.
  match_snippet: string | null
  // Where match_snippet's winning hit lives, so the sidebar can deep-link the snippet click to
  // the matched message. Both null WHENEVER match_snippet is null; match_agent_hex_id is
  // additionally null for a main-transcript hit, non-null only for a subagent-transcript hit
  // (routes the deep link through /a/{hex}/, mirroring HitOut.agent_hex_id).
  match_record_uuid: string | null
  match_agent_hex_id: string | null
  origin: SessionOrigin
}

export interface TranscriptInfo {
  id: number
  kind: string
  agent_hex_id: string | null
  agent_type: string | null
  agent_description: string | null
  parent_tool_use_id: string | null
}

export interface SessionDetail extends SessionSummary {
  transcripts: TranscriptInfo[]
  on_disk: boolean
}

export interface BlockOut {
  block_index: number
  block_kind: string
  text_content: string | null
  tool_name: string | null
  tool_use_id: string | null
  is_error: boolean | null
}

export interface MessageOut {
  record_uuid: string
  parent_uuid: string | null
  type: string
  model: string | null
  timestamp: string | null
  blocks: BlockOut[]
  authorship_kind: string | null
  authorship_basis: string | null
  authorship_detail: string | null
}

export interface HitOut {
  record_uuid: string | null
  transcript_id: number
  block_index: number
  block_kind: string
  snippet: string
  timestamp: string | null
  /** The subagent hex when this hit lives in a subagent transcript; null for the main
   * transcript. HitSnippet routes by it — a subagent hit must deep-link the /a/{hex}/ path. */
  agent_hex_id: string | null
}

export interface Problem {
  status: number
  title: string
  detail: string
}

export type ResumeMode = 'launched' | 'missing_cwd' | 'open_failed' | 'unsupported_platform'

export interface ResumeResult {
  restored: boolean
  launched: boolean
  mode: ResumeMode
  command: string
  cwd: string | null
  live_path: string
  detail: string | null
}

// --- server/src/introspect/api/routes/sessions.py (route-local envelopes) ---------------

export interface ProjectOut {
  id: number
  dir_slug: string
  resolved_cwd: string | null
  session_count: number
}

// Task T14: counts by origin over the CURRENT query's filters (q/favorite/projects) but
// WITHOUT the origin filter itself applied — so the sidebar can report "N subagent sessions
// hidden" against the filtered set the reveal control is about to widen, not the whole archive.
export interface SessionOriginCounts {
  root: number
  subagent: number
  empty: number
}

export interface SessionList {
  items: SessionSummary[]
  total: number
  origin_counts: SessionOriginCounts
}

export interface MessageList {
  items: MessageOut[]
  total: number
  offset: number
}

// --- server/src/introspect/api/routes/search.py (route-local envelopes) -----------------

/** Mirrors the `scope` query param's `Literal["global", "session"]`. */
export type SearchScope = 'global' | 'session'

export interface SearchGroup {
  session: SessionSummary
  hits: HitOut[]
  has_more: boolean
}

export interface GlobalSearchResult {
  groups: SearchGroup[]
  total: number
}

export interface SessionSearchResult {
  items: HitOut[]
  total: number
}

// --- server/src/introspect/api/routes/admin.py (route-local envelopes) ------------------

export interface ImportRun {
  id: number
  trigger: string
  status: string
  started_at: string | null
  finished_at: string | null
  files_seen: number
  records_added: number
  records_skipped_duplicate: number
  anomaly_count: number
}

export interface TriggerImportOut {
  run_id: number
}

export interface AnomalyBreakdown {
  error: number
  warn: number
  info: number
}

export interface StatusOut {
  version: string
  sessions: number
  files: number
  records: number
  archive_bytes: number
  anomalies: AnomalyBreakdown
  last_run: ImportRun | null
}

// --- server/src/introspect/api/models.py (memory item models) ---------------------------

export interface MemoryOut {
  name: string
  description: string | null
  type: string | null
  filename: string
  path: string
  size: number
  mtime: string | null
  body: string | null
  error: string | null
}

export interface MemoryProjectOut {
  dir_slug: string
  resolved_cwd: string | null
  memories: MemoryOut[]
}

// --- server/src/introspect/api/routes/memories.py (route-local envelope) ---------------

export interface MemoryList {
  projects: MemoryProjectOut[]
}
