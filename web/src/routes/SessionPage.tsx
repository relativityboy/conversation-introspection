import type { CSSProperties } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useSession } from '../api/hooks'
import { HorizonBand } from '../components/HorizonBand'
import {
  ConversationSearch,
  ConversationSearchResults,
} from '../components/search/ConversationSearch'
import { ChatOnlyToggle } from '../components/reader/ChatOnlyToggle'
import { ConversationView } from '../components/reader/ConversationView'
import { TranscriptsProvider } from '../components/reader/transcripts-context'
import { TitleEditor } from '../components/TitleEditor'
import { useChatOnly } from '../lib/chatOnly'

const MIST_TEXT: CSSProperties = { color: 'var(--mist)', fontSize: 13, padding: '18px 24px' }

// The reading room: session header (title, mono metadata, full HorizonBand) above the MAIN
// transcript's ConversationView. Subagent transcripts are Task 6+ material — only kind==='main'
// renders here.
export function SessionPage() {
  const { uuid = '', msgUuid } = useParams()
  const [searchParams] = useSearchParams()
  const q = searchParams.get('q') ?? ''
  const query = useSession(uuid)
  // The ONE owner of conversation-only state for this reader page (plan critique F4): the header
  // toggle and the ConversationView body both read this same [chatOnly, setChatOnly].
  const [chatOnly, setChatOnly] = useChatOnly()

  if (query.isPending) return <p style={MIST_TEXT}>…</p>

  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <div style={MIST_TEXT}>
          <p style={{ marginTop: 0 }}>This conversation isn&rsquo;t in the archive.</p>
          <Link to="/" style={{ color: 'var(--dragonfly)' }}>
            ← back to the archive
          </Link>
        </div>
      )
    }
    return <p style={MIST_TEXT}>archive offline</p>
  }

  const session = query.data
  const main = session.transcripts.find((t) => t.kind === 'main')

  // Body precedence: a `/m/` deep link ALWAYS wins — clicking a search hit must open the
  // conversation AT that message (seeding the around-window; ConversationView glows the target),
  // even though `q` rides along in the URL to keep the header search box populated. Absent a deep
  // link, `?q=` switches the body to the conversation-scoped results panel.
  function renderBody() {
    if (!main) return <p style={MIST_TEXT}>No transcript recorded for this session.</p>
    if (msgUuid)
      return (
        <ConversationView
          transcriptId={main.id}
          initialAroundUuid={msgUuid}
          chatOnly={chatOnly}
          setChatOnly={setChatOnly}
        />
      )
    // trim(): useSearch gates on q.trim(), so a whitespace-only ?q= (e.g. ?q=%20) would mount
    // the results panel with a query that never fires — an eternal pending "…". Fall through to
    // the conversation instead.
    if (q.trim()) return <ConversationSearchResults sessionUuid={session.session_uuid} q={q} />
    return (
      <ConversationView transcriptId={main.id} chatOnly={chatOnly} setChatOnly={setChatOnly} />
    )
  }

  // Publish the transcript inventory (and the session uuid the subagent links need) for the
  // reader's SubagentChip join — see transcripts-context. Prop-drilling through the virtualized
  // MessageTurn tree would be the wrong shape, so it rides context instead.
  return (
    <TranscriptsProvider
      value={{ sessionUuid: session.session_uuid, transcripts: session.transcripts }}
    >
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <header style={{ padding: '18px 24px 0' }}>
          <TitleEditor session={session} />
          <div
            className="session-meta mono"
            style={{
              display: 'flex',
              gap: 14,
              fontFamily: 'var(--mono)',
              fontSize: 11,
              color: 'var(--mist)',
              marginTop: 6,
            }}
          >
            <span>{session.session_uuid.slice(0, 8)}</span>
            {/* Always the UNFILTERED archive count — never a second server count for the filtered
              set (critique #6). While conversation-only is active, a mist suffix marks it. */}
            <span>
              {session.message_count} msgs{chatOnly ? ' · conversation only' : ''}
            </span>
            {/* The archive's headline capability, one glance from every conversation: the raw
              records back out as JSONL. Plain <a> (not router Link) — it's an API endpoint. */}
            <a
              href={`/api/v1/sessions/${session.session_uuid}/export.jsonl`}
              style={{ color: 'var(--dragonfly)', textDecoration: 'none' }}
            >
              ↓ .jsonl
            </a>
            <ChatOnlyToggle chatOnly={chatOnly} setChatOnly={setChatOnly} />
          </div>
          <div style={{ margin: '14px 0 6px' }}>
            <HorizonBand start={session.started_at} end={session.last_activity_at} variant="full" />
          </div>
          <div style={{ margin: '10px 0 14px' }}>
            <ConversationSearch sessionUuid={session.session_uuid} />
          </div>
        </header>
        <div style={{ flex: 1, minHeight: 0 }}>{renderBody()}</div>
      </div>
    </TranscriptsProvider>
  )
}
