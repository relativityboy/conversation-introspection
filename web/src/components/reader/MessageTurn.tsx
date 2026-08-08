import type { CSSProperties, MouseEvent } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { BlockOut, MessageOut } from '../../api/types'
import { isVisibleInView, type ViewMode } from '../../lib/viewMode'
import { ImageBlock } from './ImageBlock'
import { MarkdownProse } from './MarkdownProse'
import { SubagentChip } from './SubagentChip'
import { ThinkingGlyph } from './ThinkingGlyph'
import { ToolBlock } from './ToolBlock'
import './eyebrow.css'

// NOTE(claude): speaker labels are deliberately generic — "YOU" / "CLAUDE" / "SYSTEM", never
// personal names. This repo is public and reads whatever archive it's pointed at; other
// people's archives aren't relativityboy's, so the reader must not bake anyone's identity in.
//
// "attachment" is the fourth voice (Task P4-F1): a block-bearing attachment is a queued command
// the human typed that the harness delivered as a system record. It is labelled SYSTEM (YOU) —
// materially system-delivered, but source-accurate to the human's own words — and takes the dawn
// (user) accent. A zero-block attachment is harness furniture and resolves to plain 'system'.
type Voice = 'user' | 'assistant' | 'system' | 'attachment'

const SPEAKER: Record<Voice, string> = {
  user: 'YOU',
  assistant: 'CLAUDE',
  system: 'SYSTEM',
  attachment: 'SYSTEM (YOU)',
}

const ACCENT: Record<Voice, string> = {
  user: 'var(--dawn)',
  assistant: 'var(--dragonfly)',
  system: 'var(--mist)',
  attachment: 'var(--dawn)',
}

function voiceOf(message: MessageOut): Voice {
  const { type } = message
  if (type === 'user' || type === 'assistant') return type
  // Only an attachment that carried interpreted content (a rescued human queued prompt) gets the
  // SYSTEM (YOU) voice; a blockless attachment stays plain SYSTEM, like any other furniture.
  if (type === 'attachment' && message.blocks.length > 0) return 'attachment'
  return 'system'
}

const EYEBROW_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 10,
  letterSpacing: '.14em',
  color: 'var(--mist)',
}

// route context → this row's shareable path; null outside a session route (bare unit renders)
function useEntryHref(recordUuid: string): string | null {
  const { uuid, agentHex } = useParams()
  if (!uuid) return null
  return agentHex ? `/s/${uuid}/a/${agentHex}/m/${recordUuid}` : `/s/${uuid}/m/${recordUuid}`
}

export interface MessageTurnProps {
  message: MessageOut
  /** Reader view mode (authorship spec §5): gates both whole-message visibility
   * (`isVisibleInView`) and block-level hiding of tool_use/tool_result — see `Block`. Owned by the
   * page via useViewMode; defaults to 'all' (show everything) when omitted, matching the
   * un-virtualized unit tests that predate this filtering. */
  view?: ViewMode
  /** Opens the raw-record inspector for this row (§15.2), wired to the speaker-name button.
   * Supplied by the reader (MessageStream); absent in the un-virtualized unit tests, where the
   * name renders as plain text instead. */
  onInspect?: (recordUuid: string) => void
}

export function MessageTurn({ message, view = 'all', onInspect }: MessageTurnProps) {
  const href = useEntryHref(message.record_uuid)
  const [copied, setCopied] = useState(false)
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (copiedTimerRef.current !== null) clearTimeout(copiedTimerRef.current)
    }
  }, [])

  // Deliberate new-tab/copy-link gestures stay native (spec §5): only a plain primary click
  // copies. Middle-click/right-click never reach onClick.
  function handleTimeClick(e: MouseEvent<HTMLAnchorElement>) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !href) return
    e.preventDefault()
    void navigator.clipboard?.writeText(window.location.origin + href).catch(() => {})
    setCopied(true)
    if (copiedTimerRef.current !== null) clearTimeout(copiedTimerRef.current)
    copiedTimerRef.current = setTimeout(() => {
      copiedTimerRef.current = null
      setCopied(false)
    }, 1600)
  }

  // A filtered view hides rows whose authorship kind/type doesn't qualify OR that show no content
  // there (spec §4/§5): thinking-only / tool-only / empty-text rows collapse to nothing, including
  // the ~800 zero-block deferred_tools_delta / skill_listing / task_reminder attachment stubs,
  // while a block-bearing attachment (a rescued human queued prompt) stays. `isVisibleInView` is
  // the SAME predicate the raw inspector's prev/next uses (lib/viewMode) and mirrors the server's
  // `_view_filter`, so the rows this reader hides and the rows that navigation skips can never
  // drift.
  if (!isVisibleInView(message, view)) return null

  const voice = voiceOf(message)
  const time = localHHMM(message.timestamp)
  const blocks = [...message.blocks].sort((a, b) => a.block_index - b.block_index)

  // 28px inter-turn spacing lives as PADDING on the article, not margin: react-virtuoso
  // measures item border-boxes, and margins would collapse/escape the measurement. The accent
  // border sits on the inner div so it doesn't run through the spacing gap.
  return (
    <article className={`message-turn turn-${voice}`} style={{ paddingBottom: 28 }}>
      <div style={{ borderLeft: `2px solid ${ACCENT[voice]}`, paddingLeft: 16 }}>
        <div
          className="turn-eyebrow-row"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 8,
          }}
        >
          <span className="turn-eyebrow mono" style={EYEBROW_STYLE}>
            {onInspect ? (
              <button
                type="button"
                className="turn-speaker sw-tip"
                data-tip="view raw record"
                aria-label={`view raw record — ${SPEAKER[voice]}`}
                onClick={() => onInspect(message.record_uuid)}
              >
                {SPEAKER[voice]}
              </button>
            ) : (
              SPEAKER[voice]
            )}
            {time && (
              <>
                {' · '}
                {href ? (
                  <a
                    className="turn-time sw-tip"
                    data-tip="click to copy deeplink"
                    href={href}
                    onClick={handleTimeClick}
                  >
                    {time}
                  </a>
                ) : (
                  time
                )}
                {copied && <span className="turn-copied">copied</span>}
              </>
            )}
          </span>
        </div>
        {blocks.map((block) => (
          <Block key={block.block_index} block={block} view={view} />
        ))}
      </div>
    </article>
  )
}

// Per-kind dispatch. tool_use routes to SubagentChip, which resolves the transcript join and
// falls back to ToolBlock when the block is an ordinary tool call (not a subagent dispatch).
// Unknown kinds render a mono chip rather than throwing — the archive may grow block kinds this
// reader predates, and a forward-tolerant marker beats a crash.
//
// Outside `all`, tool_use and tool_result are dropped block-level (subagent chips disappear WITH
// their tool_use — ledger #7, intended, not special-cased) in BOTH filtered views alike: `chat`
// and `chat-harness` differ only in which whole MESSAGES survive `isVisibleInView`, not in which
// blocks a surviving message shows. text / thinking / image (and forward-tolerant unknowns) still
// render — this is a per-block visual filter, distinct from the server's message-level `view`
// filter on the seed.
function Block({ block, view }: { block: BlockOut; view: ViewMode }) {
  switch (block.block_kind) {
    case 'text':
      return block.text_content ? <MarkdownProse markdown={block.text_content} /> : null
    case 'thinking':
      return <ThinkingGlyph />
    case 'image':
      return <ImageBlock />
    case 'tool_use':
      return view === 'all' ? <SubagentChip block={block} /> : null
    case 'tool_result':
      return view === 'all' ? <ToolBlock block={block} /> : null
    default:
      return <UnknownChip kind={block.block_kind} />
  }
}

function UnknownChip({ kind }: { kind: string }) {
  return (
    <div
      className="block-unknown mono"
      style={{
        fontFamily: 'var(--mono)',
        fontSize: 11,
        color: 'var(--mist)',
        margin: '6px 0',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
    >
      [{kind}]
    </div>
  )
}

/** Local wall-clock "HH:MM" for the eyebrow; null when the timestamp is absent or unparsable. */
function localHHMM(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}
