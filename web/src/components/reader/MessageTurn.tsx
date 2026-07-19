import type { BlockOut, MessageOut } from '../../api/types'
import { ImageBlock } from './ImageBlock'
import { MarkdownProse } from './MarkdownProse'
import { SubagentChip } from './SubagentChip'
import { ThinkingGlyph } from './ThinkingGlyph'
import { ToolBlock } from './ToolBlock'

// NOTE(claude): speaker labels are deliberately generic — "YOU" / "CLAUDE" / "SYSTEM", never
// personal names. This repo is public and reads whatever archive it's pointed at; other
// people's archives aren't Donovan's, so the reader must not bake anyone's identity in.
type Role = 'user' | 'assistant' | 'system'

const SPEAKER: Record<Role, string> = { user: 'YOU', assistant: 'CLAUDE', system: 'SYSTEM' }

const ACCENT: Record<Role, string> = {
  user: 'var(--dawn)',
  assistant: 'var(--dragonfly)',
  system: 'var(--mist)',
}

function roleOf(type: string): Role {
  if (type === 'user' || type === 'assistant') return type
  return 'system'
}

export interface MessageTurnProps {
  message: MessageOut
}

export function MessageTurn({ message }: MessageTurnProps) {
  const role = roleOf(message.type)
  const time = localHHMM(message.timestamp)
  const blocks = [...message.blocks].sort((a, b) => a.block_index - b.block_index)

  // 28px inter-turn spacing lives as PADDING on the article, not margin: react-virtuoso
  // measures item border-boxes, and margins would collapse/escape the measurement. The accent
  // border sits on the inner div so it doesn't run through the spacing gap.
  return (
    <article className={`message-turn turn-${role}`} style={{ paddingBottom: 28 }}>
      <div style={{ borderLeft: `2px solid ${ACCENT[role]}`, paddingLeft: 16 }}>
        <div
          className="turn-eyebrow mono"
          style={{
            fontFamily: 'var(--mono)',
            fontSize: 10,
            letterSpacing: '.14em',
            color: 'var(--mist)',
            marginBottom: 8,
          }}
        >
          {time ? `${SPEAKER[role]} · ${time}` : SPEAKER[role]}
        </div>
        {blocks.map((block) => (
          <Block key={block.block_index} block={block} />
        ))}
      </div>
    </article>
  )
}

// Per-kind dispatch. tool_use routes to SubagentChip, which resolves the transcript join and
// falls back to ToolBlock when the block is an ordinary tool call (not a subagent dispatch).
// Unknown kinds render a mono chip rather than throwing — the archive may grow block kinds this
// reader predates, and a forward-tolerant marker beats a crash.
function Block({ block }: { block: BlockOut }) {
  switch (block.block_kind) {
    case 'text':
      return block.text_content ? <MarkdownProse markdown={block.text_content} /> : null
    case 'thinking':
      return <ThinkingGlyph />
    case 'image':
      return <ImageBlock />
    case 'tool_use':
      return <SubagentChip block={block} />
    case 'tool_result':
      return <ToolBlock block={block} />
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
