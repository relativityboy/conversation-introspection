import type { CSSProperties } from 'react'
import type { BlockOut, MessageOut } from '../../api/types'
import { isChatOnlyVisible } from '../../lib/chatOnly'
import { ImageBlock } from './ImageBlock'
import { MarkdownProse } from './MarkdownProse'
import { SubagentChip } from './SubagentChip'
import { ThinkingGlyph } from './ThinkingGlyph'
import { ToolBlock } from './ToolBlock'

// NOTE(claude): speaker labels are deliberately generic — "YOU" / "CLAUDE" / "SYSTEM", never
// personal names. This repo is public and reads whatever archive it's pointed at; other
// people's archives aren't Donovan's, so the reader must not bake anyone's identity in.
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

// A quiet mono `{}` in the eyebrow that opens the raw-record inspector (§15.2). Mist-toned, no
// chrome — the calmest possible affordance, one per row, wired only when the reader supplies an
// onInspect (SubagentPage/SessionPage readers do; the un-virtualized MessageTurn unit tests don't).
const INSPECT_BUTTON_STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 10,
  lineHeight: 1.2,
  letterSpacing: '.06em',
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: 'var(--mist)',
}

export interface MessageTurnProps {
  message: MessageOut
  /** Conversation-only mode: hide tool_use / tool_result blocks (and the subagent chips that ride
   * tool_use) — see `Block`. Owned by the page via useChatOnly; false when omitted. */
  chatOnly?: boolean
  /** Opens the raw-record inspector for this row (§15.2). Supplied by the reader (MessageStream);
   * absent in the un-virtualized unit tests, where the `{}` affordance simply isn't rendered. */
  onInspect?: (recordUuid: string) => void
}

export function MessageTurn({ message, chatOnly = false, onInspect }: MessageTurnProps) {
  // Conversation-only mode hides harness-furniture attachment stubs (Task P4-F1): the ~800
  // zero-block deferred_tools_delta / skill_listing / task_reminder rows collapse to nothing,
  // while a block-bearing attachment (a rescued human queued prompt) stays. `isChatOnlyVisible` is
  // the SAME predicate the raw inspector's prev/next uses (lib/chatOnly), so the rows this reader
  // hides and the rows that navigation skips can never drift.
  if (chatOnly && !isChatOnlyVisible(message)) return null

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
            justifyContent: 'space-between',
            gap: 8,
            marginBottom: 8,
          }}
        >
          <span
            className="turn-eyebrow mono"
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 10,
              letterSpacing: '.14em',
              color: 'var(--mist)',
            }}
          >
            {time ? `${SPEAKER[voice]} · ${time}` : SPEAKER[voice]}
          </span>
          {onInspect && (
            <button
              type="button"
              className="raw-record-open mono"
              aria-label="Inspect raw record"
              onClick={() => onInspect(message.record_uuid)}
              style={INSPECT_BUTTON_STYLE}
            >
              {'{}'}
            </button>
          )}
        </div>
        {blocks.map((block) => (
          <Block key={block.block_index} block={block} chatOnly={chatOnly} />
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
// Under `chatOnly`, tool_use and tool_result are dropped block-level (subagent chips disappear
// WITH their tool_use — ledger #7, intended, not special-cased). text / thinking / image (and
// forward-tolerant unknowns) still render — this is a per-block visual filter, distinct from the
// server's message-level `chat_only` filter on the seed.
function Block({ block, chatOnly }: { block: BlockOut; chatOnly: boolean }) {
  switch (block.block_kind) {
    case 'text':
      return block.text_content ? <MarkdownProse markdown={block.text_content} /> : null
    case 'thinking':
      return <ThinkingGlyph />
    case 'image':
      return <ImageBlock />
    case 'tool_use':
      return chatOnly ? null : <SubagentChip block={block} />
    case 'tool_result':
      return chatOnly ? null : <ToolBlock block={block} />
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
