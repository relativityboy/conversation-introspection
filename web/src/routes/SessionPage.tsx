import type { CSSProperties } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useSession } from '../api/hooks'
import { HorizonBand } from '../components/HorizonBand'
import {
  ConversationSearch,
  ConversationSearchResults,
} from '../components/search/ConversationSearch'
import { ActionsMenu } from '../components/ActionsMenu'
import { ChatOnlyToggle } from '../components/reader/ChatOnlyToggle'
import { ConversationView } from '../components/reader/ConversationView'
import { TranscriptsProvider } from '../components/reader/transcripts-context'
import { TitleEditor } from '../components/TitleEditor'
import { useChatOnly } from '../lib/chatOnly'
import { readProjects, writeProjects } from '../lib/urlState'

const MIST_TEXT: CSSProperties = { color: 'var(--mist)', fontSize: 13, padding: '18px 24px' }

// The reading room: session header (title, mono metadata, full HorizonBand) above the MAIN
// transcript's ConversationView. Subagent transcripts are Task 6+ material — only kind==='main'
// renders here.
export function SessionPage() {
  const { uuid = '', msgUuid } = useParams()
  const [searchParams] = useSearchParams()
  const q = searchParams.get('q') ?? ''
  // Phase 4 fixwave THE IMPORTANT (half 2): "/" below is a direct link, not routed through
  // App.tsx's catch-all redirect, so it must carry the project filter itself -- deliberately NOT
  // q (TabBar's established precedent: switching surfaces resets search boxes).
  const backToArchiveSearch = writeProjects(
    new URLSearchParams(),
    readProjects(searchParams),
  ).toString()
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
          <Link
            to={{ pathname: '/', search: backToArchiveSearch }}
            style={{ color: 'var(--dragonfly)' }}
          >
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
              set (critique #6). "total" is what keeps that honest when conversation-only hides
              rows, and it is UNCONDITIONAL on purpose: a suffix that appears on toggle widened
              this span by 132px and shoved every control right, so the (correctly highlighted)
              toggle jumped into empty space and read as a NEW button. The button says which mode
              you are in; this says what the number means. One concept, one place, no reflow. */}
            <span>{session.message_count} msgs total</span>
            {/* §17: the door back in. Archived sessions never render this page (§15.1 detail
              404), so no archived-branch is needed here — the hiding is structural. §15.1: the
              archive affordance inside this menu has no confirm dialog — the action is
              reversible out-of-band (`introspect unarchive`). Resume, the .jsonl export, and
              archive all live inside this actions ▾ panel now (spec §3.1) — see ActionsMenu for
              the per-item detail. */}
            <ActionsMenu session={session} backSearch={backToArchiveSearch} />
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
