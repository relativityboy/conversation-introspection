/**
 * TS mirrors of the server's response shapes.
 *
 * Field names/optionality are read from the Pydantic source, not memory — see the section
 * comments below for exactly which Python file/class each type mirrors. Datetimes serialize
 * to ISO-8601 strings (Pydantic v2's default `datetime` JSON encoding), typed here as
 * `string` (never/null per the source field's optionality).
 */

// --- server/src/introspect/api/models.py -------------------------------------------------

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

// --- server/src/introspect/api/routes/sessions.py (route-local envelopes) ---------------

export interface ProjectOut {
  id: number
  dir_slug: string
  resolved_cwd: string | null
  session_count: number
}

export interface SessionList {
  items: SessionSummary[]
  total: number
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
  sessions: number
  files: number
  records: number
  archive_bytes: number
  anomalies: AnomalyBreakdown
  last_run: ImportRun | null
}
