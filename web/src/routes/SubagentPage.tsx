import type { CSSProperties } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useSession } from '../api/hooks'
import { ConversationView } from '../components/reader/ConversationView'
import { TranscriptsProvider } from '../components/reader/transcripts-context'

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
  const query = useSession(uuid)

  if (query.isPending) return <p style={MIST_TEXT}>…</p>
  if (query.isError) return <p style={MIST_TEXT}>archive offline</p>

  const session = query.data
  const transcript = session.transcripts.find(
    (t) => t.kind === 'subagent' && t.agent_hex_id === agentHex,
  )

  // Plain breadcrumb — no param carry: unlike SessionPage's `/m/` deep links, a not-found
  // subagent has nothing worth preserving in the back-link (no q, no msgUuid — the target
  // conversation is the parent session's MAIN transcript, not this one).
  if (!transcript) {
    return (
      <div style={MIST_TEXT}>
        <p style={{ marginTop: 0 }}>This subagent transcript isn&rsquo;t in the archive.</p>
        <Link to={`/s/${uuid}`} style={{ color: 'var(--dragonfly)' }}>
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
          <Link to={`/s/${uuid}`} style={BREADCRUMB_STYLE}>
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
              fontFamily: 'var(--mono)',
              fontSize: 11,
              color: 'var(--mist)',
              marginTop: 8,
              marginBottom: 14,
            }}
          >
            {transcript.agent_hex_id?.slice(0, 8)}
          </div>
        </header>
        <div style={{ flex: 1, minHeight: 0 }}>
          {/* The lazy contract: this is the ONLY place this transcript's messages are fetched —
              nothing about it loads until this route mounts. */}
          <ConversationView transcriptId={transcript.id} initialAroundUuid={msgUuid} />
        </div>
      </div>
    </TranscriptsProvider>
  )
}
