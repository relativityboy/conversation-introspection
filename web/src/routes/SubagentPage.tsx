import type { CSSProperties } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useSession } from '../api/hooks'
import { ChatOnlyToggle } from '../components/reader/ChatOnlyToggle'
import { ConversationView } from '../components/reader/ConversationView'
import { TranscriptsProvider } from '../components/reader/transcripts-context'
import { useChatOnly } from '../lib/chatOnly'
import { readProjects, writeProjects } from '../lib/urlState'

const MIST_TEXT: CSSProperties = { color: 'var(--mist)', fontSize: 13, padding: '18px 24px' }
const BREADCRUMB_STYLE: CSSProperties = {
  display: 'inline-block',
  fontFamily: 'var(--mono)',
  fontSize: 12,
  color: 'var(--dragonfly)',
  textDecoration: 'none',
}

// The subagent drill-in: a single subagent transcript, reached via SubagentChip's "view
// transcript →" link. Mirrors SessionPage's shape (header above a ConversationView) but resolves
// its transcript id from the SAME SessionDetail the parent conversation already fetched — no new
// "get one transcript" endpoint exists, so this page re-fetches the session (react-query's cache
// makes that a no-op when arriving from within the session) and finds the matching row itself.
export function SubagentPage() {
  const { uuid = '', agentHex = '', msgUuid } = useParams()
  const [searchParams] = useSearchParams()
  // Carried onto both "← back to conversation" breadcrumbs below (Task 9) AND the "← back to the
  // archive" link a few lines down (Phase 4 fixwave THE IMPORTANT, half 2): "/" is a direct link,
  // not routed through App.tsx's catch-all redirect, so it must carry the filter itself.
  const backSearch = writeProjects(new URLSearchParams(), readProjects(searchParams)).toString()
  const query = useSession(uuid)
  // The ONE owner of conversation-only state for this reader page (plan critique F4), same shape
  // as SessionPage — the header toggle and the ConversationView body share this single state.
  const [chatOnly, setChatOnly] = useChatOnly()

  if (query.isPending) return <p style={MIST_TEXT}>…</p>

  if (query.isError) {
    // Mirrors SessionPage's error split: a 404 is a not-found (unknown session uuid), not the
    // archive being unreachable — same text/back-link so both routes read identically.
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <div style={MIST_TEXT}>
          <p style={{ marginTop: 0 }}>This conversation isn&rsquo;t in the archive.</p>
          <Link to={{ pathname: '/', search: backSearch }} style={{ color: 'var(--dragonfly)' }}>
            ← back to the archive
          </Link>
        </div>
      )
    }
    return <p style={MIST_TEXT}>archive offline</p>
  }

  const session = query.data
  const transcript = session.transcripts.find(
    (t) => t.kind === 'subagent' && t.agent_hex_id === agentHex,
  )

  // Otherwise a plain breadcrumb — no q/msgUuid carry: unlike SessionPage's `/m/` deep links, a
  // not-found subagent has nothing worth preserving there (the target conversation is the parent
  // session's MAIN transcript, not this one). `projects=` still carries (Task 9; it's app-level
  // filter state, not something specific to this failed lookup).
  if (!transcript) {
    return (
      <div style={MIST_TEXT}>
        <p style={{ marginTop: 0 }}>This subagent transcript isn&rsquo;t in the archive.</p>
        <Link to={{ pathname: `/s/${uuid}`, search: backSearch }} style={{ color: 'var(--dragonfly)' }}>
          ← back to conversation
        </Link>
      </div>
    )
  }

  // NOTE(claude): wrapped in its own TranscriptsProvider (not just inheriting SessionPage's,
  // which doesn't render this route at all) carrying the FULL session transcript inventory, not
  // just this one. A subagent's own transcript can itself dispatch further subagents — those
  // nested SubagentChip "view transcript →" joins (parent_tool_use_id → transcript) resolve
  // against the same session-wide inventory, per the plan's binding note for this task.
  return (
    <TranscriptsProvider
      value={{ sessionUuid: session.session_uuid, transcripts: session.transcripts }}
    >
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <header style={{ padding: '18px 24px 0' }}>
          <Link to={{ pathname: `/s/${uuid}`, search: backSearch }} style={BREADCRUMB_STYLE}>
            ← back to conversation
          </Link>
          <h1
            className="mono"
            style={{
              margin: '10px 0 0',
              fontFamily: 'var(--mono)',
              fontSize: 18,
              fontWeight: 600,
              color: 'var(--moonpaper)',
            }}
          >
            ⑂ {transcript.agent_type ?? 'subagent'}
          </h1>
          {transcript.agent_description && (
            <p
              style={{
                fontFamily: 'var(--serif)',
                fontSize: 15,
                lineHeight: 1.5,
                color: 'var(--moonpaper)',
                margin: '6px 0 0',
                maxWidth: '72ch',
              }}
            >
              {transcript.agent_description}
            </p>
          )}
          <div
            className="mono"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              fontFamily: 'var(--mono)',
              fontSize: 11,
              color: 'var(--mist)',
              marginTop: 8,
              marginBottom: 14,
            }}
          >
            {/* No mode suffix here. SessionPage's marker exists to keep an UNFILTERED message
              count honest (critique #6); this row has no count, so copying the marker only
              painted "conversation only" twice inches apart — the toggle beside it already
              carries the state, highlighted and aria-pressed. */}
            <span>{transcript.agent_hex_id?.slice(0, 8)}</span>
            <ChatOnlyToggle chatOnly={chatOnly} setChatOnly={setChatOnly} />
          </div>
        </header>
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* The lazy contract: this is the ONLY place this transcript's messages are fetched —
              nothing about it loads until this route mounts. */}
          <ConversationView
            transcriptId={transcript.id}
            initialAroundUuid={msgUuid}
            chatOnly={chatOnly}
            setChatOnly={setChatOnly}
          />
        </div>
      </div>
    </TranscriptsProvider>
  )
}
