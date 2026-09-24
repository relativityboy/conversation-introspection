import type { CSSProperties, MouseEvent } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { BlockOut, MessageOut } from '../../api/types'
import { normalizeChatFences } from '../../lib/chatFences'
import {
  ALL_CATEGORIES_SET,
  CLAUDE_KINDS,
  HUMAN_KINDS,
  categoryOfBlock,
  isVisibleInSelection,
  type CategorySlug,
} from '../../lib/viewMode'
import { ImageBlock } from './ImageBlock'
import { MarkdownProse } from './MarkdownProse'
import { SubagentChip } from './SubagentChip'
import { ThinkingGlyph } from './ThinkingGlyph'
import { ToolBlock } from './ToolBlock'
import { useDispatchToolUseIds } from './transcripts-context'
import './eyebrow.css'

// NOTE(claude): speaker labels are deliberately generic — "YOU" / "CLAUDE" / "SYSTEM", never
// personal names. This repo is public and reads whatever archive it's pointed at; other
// people's archives aren't relativityboy's, so the reader must not bake anyone's identity in.
// `speakerFor` (below) — the authorship spec's §3.3 kind→label/accent map — is the ONE source of
// truth for what a row's eyebrow says; a message not yet backfilled with an `authorship_kind`
// (migrate→reparse deploy window) falls back to `legacyVoiceOf`'s coarser type-based read, never
// to a name.
//
// "attachment" is the fourth voice (Task P4-F1): a block-bearing attachment is a queued command
// the human typed that the harness delivered as a system record. It is labelled SYSTEM (YOU) —
// materially system-delivered, but source-accurate to the human's own words — and takes the dawn
// (user) accent. A zero-block attachment is harness furniture and resolves to plain 'system'.
// Once a message is classified, `speakerFor` reaches the same two outcomes via the
// `attachment_queued_human` / `attachment_furniture` kinds; `legacyVoiceOf` is only the
// pre-classification (null-kind) read of the same distinction.
type Voice = 'user' | 'assistant' | 'system' | 'attachment'

const LEGACY_SPEAKER: Record<Voice, string> = {
  user: 'YOU',
  assistant: 'CLAUDE',
  system: 'SYSTEM',
  attachment: 'SYSTEM (YOU)',
}

const LEGACY_ACCENT: Record<Voice, string> = {
  user: 'var(--dawn)',
  assistant: 'var(--dragonfly)',
  system: 'var(--mist)',
  attachment: 'var(--dawn)',
}

/** Pre-classification fallback (spec §4 deploy window): the OLD type-derived voice read. Used
 * ONLY when `authorship_kind` is null — every classified message goes through the §3.3 map
 * (`labelFor`/`accentFor`) instead. */
function legacyVoiceOf(message: MessageOut): Voice {
  const { type } = message
  if (type === 'user' || type === 'assistant') return type
  // Only an attachment that carried interpreted content (a rescued human queued prompt) gets the
  // SYSTEM (YOU) voice; a blockless attachment stays plain SYSTEM, like any other furniture.
  if (type === 'attachment' && message.blocks.length > 0) return 'attachment'
  return 'system'
}

// Kind buckets behind §3.3's accent grouping. Dawn is reserved for human-authored content — ONLY
// human_* and attachment_queued_human may take it; every other kind is dragonfly (Claude-voiced)
// or mist (everything else). HUMAN_KINDS/CLAUDE_KINDS now live in lib/viewMode.ts (Task T10) so
// the eyebrow's voice grouping and the category filter's chat-category mapping can never drift —
// imported above rather than redefined here.

// Task 1 (chat-fence-normalizer plan): which kinds get their text blocks run through
// `normalizeChatFences` before rendering. Deliberately a SEPARATE set from `HUMAN_KINDS` rather
// than widening it — `HUMAN_KINDS` drives the eyebrow's dawn-accent grouping (§3.3), a labeling
// concern with its own three-kind contract pinned by tests elsewhere. `attachment_queued_human`
// belongs HERE (its text is the owner's own typed/queued words, materially delivered by the
// harness, so it can carry the same chat-style fence typos) but not in `HUMAN_KINDS`: that set's
// callers (`accentFor`, `voiceClassOf`) already special-case `attachment_queued_human` on its own
// for the fourth "attachment" voice, and folding it into `HUMAN_KINDS` would change that voice
// logic's shape for no reason connected to fence normalization.
const FENCE_NORMALIZED_KINDS = new Set([
  'human_typed',
  'human_queued',
  'human_inferred',
  'attachment_queued_human',
])

function accentFor(kind: string): string {
  if (HUMAN_KINDS.has(kind) || kind === 'attachment_queued_human') return 'var(--dawn)'
  if (CLAUDE_KINDS.has(kind)) return 'var(--dragonfly)'
  return 'var(--mist)'
}

// The article's `turn-*` CSS class bucket — a styling/test hook, deliberately decoupled from the
// label/accent logic below. Mirrors the legacy 4-voice split over the full kind set:
// `attachment_queued_human` keeps the historical "fourth voice" class; everything else that isn't
// clearly human- or Claude-voiced (including `attachment_furniture`) buckets to plain 'system'.
function voiceClassOf(message: MessageOut): Voice {
  const kind = message.authorship_kind
  if (kind == null) return legacyVoiceOf(message)
  if (HUMAN_KINDS.has(kind)) return 'user'
  if (kind === 'attachment_queued_human') return 'attachment'
  if (CLAUDE_KINDS.has(kind)) return 'assistant'
  return 'system'
}

/** §3.3: kind + detail → eyebrow label. Qualifier text (skill/command/tool names) renders
 * lowercase; a skill's detail is the substring after the LAST `:` (plugin prefix stripped, e.g.
 * `superpowers:brainstorming` → `brainstorming`); `system`'s free-text subtype uppercases with
 * `_` → space; `interrupt_marker` ignores its detail entirely — it is always plain
 * SYSTEM (INTERRUPT), never words attributed to a tool. */
function labelFor(kind: string, detail: string | null): string {
  switch (kind) {
    case 'human_typed':
    case 'human_queued':
    case 'human_inferred':
      return 'YOU'
    case 'claude':
      return 'CLAUDE'
    case 'dispatch':
      return 'CLAUDE (DISPATCH)'
    case 'coordinator':
      return 'CLAUDE (COORDINATOR)'
    case 'tool_result':
      return 'SYSTEM (TOOL RESULT)'
    case 'skill_injection':
      return detail ? `SYSTEM (SKILL: ${lastSegment(detail)})` : 'SYSTEM (SKILL)'
    case 'tool_injection':
      return detail ? `SYSTEM (INJECTED: ${detail.toLowerCase()})` : 'SYSTEM (INJECTED)'
    case 'task_notification':
      return 'SYSTEM (TASK NOTIFICATION)'
    case 'sdk_automation':
      return 'SYSTEM (AUTOMATION)'
    case 'command_expansion':
      return detail ? `SYSTEM (COMMAND: ${detail.toLowerCase()})` : 'SYSTEM (COMMAND)'
    case 'command_output':
      return 'SYSTEM (COMMAND OUTPUT)'
    case 'harness_meta':
      if (detail === 'reminder') return 'SYSTEM (REMINDER)'
      if (detail === 'caveat') return 'SYSTEM (CAVEAT)'
      return 'SYSTEM (META)'
    case 'interrupt_marker':
      return 'SYSTEM (INTERRUPT)'
    case 'compact_summary':
      return 'SYSTEM (COMPACTION)'
    case 'system':
      return detail ? `SYSTEM (${humanize(detail)})` : 'SYSTEM'
    case 'attachment_queued_human':
      return 'SYSTEM (YOU)'
    case 'attachment_furniture':
      return 'SYSTEM'
    case 'unclassified':
      return 'SYSTEM (UNCLASSIFIED)'
    default:
      // Forward-tolerant floor: a kind this reader predates (future classifier drift) still
      // renders as SYSTEM with its own name as the qualifier — never blank, never a throw
      // (mirrors the classifier's own total/never-raising contract, and UnknownChip's block-kind
      // equivalent below).
      return `SYSTEM (${humanize(kind)})`
  }
}

function lastSegment(detail: string): string {
  return detail.slice(detail.lastIndexOf(':') + 1).toLowerCase()
}

function humanize(value: string): string {
  return value.toUpperCase().replace(/_/g, ' ')
}

/** Produces the eyebrow's label + accent for a message (spec §3.3), exported for direct testing.
 * A non-null `authorship_kind` drives the map above; a null kind (pre-backfill row) falls back to
 * `legacyVoiceOf`. */
export function speakerFor(message: MessageOut): { label: string; accent: string } {
  const kind = message.authorship_kind
  if (kind == null) {
    const voice = legacyVoiceOf(message)
    return { label: LEGACY_SPEAKER[voice], accent: LEGACY_ACCENT[voice] }
  }
  return { label: labelFor(kind, message.authorship_detail), accent: accentFor(kind) }
}

// Task T7: display-only eyebrow override, layered ON TOP of speakerFor at render time — NOT part
// of the §3.3 kind→label map above (a thinking-only message still classifies through the normal
// `claude`/`dispatch`/`coordinator` kinds; this doesn't touch authorship data). An assistant
// message whose blocks are ALL thinking (≥1 block) reads CLAUDE (THINKING) instead of its usual
// label. A mixed message (thinking alongside text/tool blocks) keeps its ordinary label — only the
// per-block thinking rendering changes for it.
function isAllThinkingMessage(message: MessageOut): boolean {
  return (
    message.type === 'assistant' &&
    message.blocks.length > 0 &&
    message.blocks.every((block) => block.block_kind === 'thinking')
  )
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
  /** Reader category selection (Task T10, replaces the retired ViewMode `view` prop): gates both
   * whole-message visibility (`isVisibleInSelection`) and per-block rendering — see `Block`.
   * Owned by the page via the URL-backed selection hook; defaults to every category selected when
   * omitted, matching the un-virtualized unit tests that predate this filtering. */
  selection?: ReadonlySet<CategorySlug>
  /** Opens the raw-record inspector for this row (§15.2), wired to the speaker-name button.
   * Supplied by the reader (MessageStream); absent in the un-virtualized unit tests, where the
   * name renders as plain text instead. */
  onInspect?: (recordUuid: string) => void
}

export function MessageTurn({ message, selection = ALL_CATEGORIES_SET, onInspect }: MessageTurnProps) {
  const href = useEntryHref(message.record_uuid)
  const dispatchToolUseIds = useDispatchToolUseIds()
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

  // A filtered selection hides a row when NONE of its blocks' categories are selected (Task T10
  // FROZEN contract, resolved-dispatch routing added Task T12) — including EVERY zero-block row
  // (the ~800 deferred_tools_delta / skill_listing / task_reminder attachment stubs, and any
  // bare `system`-type record), which is always invisible under any selection including
  // all-five (server-confirmed divergence from the retired `all` view — see
  // `isVisibleInSelection`'s doc in viewMode.ts). A RESOLVED dispatch row with no other content
  // is now visible once `claude-chat` is selected (owner ruling 2026-09-23: a resolved-dispatch
  // `tool_use` block IS claude-chat, a doorway into a Claude-voiced conversation, not mechanical
  // traffic) — an UNRESOLVED tool_use row still needs `tool-traffic` selected, unchanged.
  // `isVisibleInSelection` needs the SAME `dispatchToolUseIds` set `Block` below reads to decide
  // this, both sourced from `useDispatchToolUseIds()` so the two can never drift on which
  // rows/blocks that is.
  if (!isVisibleInSelection(message, selection, dispatchToolUseIds)) return null

  const { label: baseLabel, accent } = speakerFor(message)
  const label = isAllThinkingMessage(message) ? 'CLAUDE (THINKING)' : baseLabel
  const voiceClass = voiceClassOf(message)
  const time = localStamp(message.timestamp)
  const blocks = [...message.blocks].sort((a, b) => a.block_index - b.block_index)

  // 28px inter-turn spacing lives as PADDING on the article, not margin: react-virtuoso
  // measures item border-boxes, and margins would collapse/escape the measurement. The accent
  // border sits on the inner div so it doesn't run through the spacing gap.
  return (
    <article className={`message-turn turn-${voiceClass}`} style={{ paddingBottom: 28 }}>
      <div style={{ borderLeft: `2px solid ${accent}`, paddingLeft: 16 }}>
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
                aria-label={`view raw record — ${label}`}
                onClick={() => onInspect(message.record_uuid)}
              >
                {label}
              </button>
            ) : (
              label
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
          <Block
            key={block.block_index}
            block={block}
            message={message}
            selection={selection}
            dispatchToolUseIds={dispatchToolUseIds}
          />
        ))}
      </div>
    </article>
  )
}

// Per-kind dispatch, gated by the Task T10/T12 category contract: a block renders iff
// `categoryOfBlock` resolves to a category in `selection` (checked once up front — every case
// below only has to decide HOW to render, never whether). tool_use ALWAYS routes through
// SubagentChip, which independently re-resolves the SAME transcript join (`useTranscripts()`) to
// decide what to draw. `categoryOfBlock` now carries a resolved-dispatch carve-out (owner ruling
// 2026-09-23): a `tool_use` block whose id is in `dispatchToolUseIds` categorizes `claude-chat`,
// an unresolved one stays `tool-traffic` — so a RESOLVED chip reaches this switch once
// `claude-chat` is selected (independent of `tool-traffic`), while an unresolved tool_use only
// reaches it when `tool-traffic` is selected. `renderFallback` (spec §6) is still exactly
// `selection.has('tool-traffic')`: it only matters inside SubagentChip's un-resolved branch, and
// by construction an unresolved tool_use can only reach this switch when tool-traffic IS
// selected, so the value already read is always right by the time it's used. Unknown block kinds
// render a mono chip rather than throwing — the archive may grow block kinds this reader
// predates, and a forward-tolerant marker beats a crash.
function Block({
  block,
  message,
  selection,
  dispatchToolUseIds,
}: {
  block: BlockOut
  message: MessageOut
  selection: ReadonlySet<CategorySlug>
  dispatchToolUseIds: ReadonlySet<string>
}) {
  if (!selection.has(categoryOfBlock(block, message, dispatchToolUseIds))) return null

  const authorshipKind = message.authorship_kind
  switch (block.block_kind) {
    case 'text': {
      if (!block.text_content) return null
      // Display-only reshaping (Task 1): the archive's stored bytes never change — the raw record
      // inspector still shows exactly what was captured. Only human-authored kinds qualify; a
      // null kind (pre-backfill row, spec §4 deploy window) is left alone rather than guessed at.
      const markdown =
        authorshipKind != null && FENCE_NORMALIZED_KINDS.has(authorshipKind)
          ? normalizeChatFences(block.text_content)
          : block.text_content
      return <MarkdownProse markdown={markdown} />
    }
    case 'thinking':
      return <ThinkingGlyph block={block} />
    case 'image':
      return <ImageBlock />
    case 'tool_use':
      return <SubagentChip block={block} renderFallback={selection.has('tool-traffic')} />
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

/** Local wall-clock "YYYY.MM.DD HH:MM" for the eyebrow; null when the timestamp is absent or
 *  unparsable. The dotted date rides with the time because the archive crosses midnight — a bare
 *  HH:MM loses which day a turn happened on. */
function localStamp(iso: string | null): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  const ymd = `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())}`
  return `${ymd} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
