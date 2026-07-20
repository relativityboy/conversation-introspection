import { Link, useSearchParams } from 'react-router-dom'
import type { BlockOut } from '../../api/types'
import { readProjects, writeProjects } from '../../lib/urlState'
import { ToolBlock } from './ToolBlock'
import { useTranscripts } from './transcripts-context'

const DESC_MAX = 60

// SubagentChip is the tool_use renderer, and it IS the dispatch-detector. A tool_use block is a
// subagent dispatch precisely when a captured transcript's parent_tool_use_id equals this block's
// tool_use_id — the join, not a tool-name string match. Consequence (deliberate): a subagent
// whose transcript was never captured has no match and degrades gracefully to a plain ToolBlock.
export function SubagentChip({ block }: { block: BlockOut }) {
  const { sessionUuid, transcripts } = useTranscripts()
  // Read directly off the URL (Task 9) rather than prop-drilling `projects` down through
  // MessageTurn's block dispatch: SubagentChip is a leaf several layers deep in the virtualized
  // reader tree, and it's always rendered within a Router (real app tree or MemoryRouter in
  // tests) — same rationale TranscriptsContext used for sessionUuid, but projects is route state,
  // not transcript state, so it doesn't belong in that context.
  const [searchParams] = useSearchParams()
  const projects = readProjects(searchParams)

  const transcript = block.tool_use_id
    ? transcripts.find((t) => t.parent_tool_use_id === block.tool_use_id)
    : undefined

  if (!transcript) return <ToolBlock block={block} />

  const agentType = transcript.agent_type ?? 'subagent'
  const desc = truncate(transcript.agent_description, DESC_MAX)

  return (
    <div
      className="subagent-chip mono"
      style={{
        display: 'flex',
        alignItems: 'baseline',
        flexWrap: 'wrap',
        gap: 8,
        fontFamily: 'var(--mono)',
        fontSize: 11,
        margin: '6px 0',
      }}
    >
      <span
        style={{
          color: 'var(--dragonfly)',
          border: '1px solid var(--shore)',
          borderRadius: 4,
          padding: '1px 6px',
        }}
      >
        ⑂ subagent · {agentType}
      </span>
      {desc && (
        <span className="subagent-desc" style={{ color: 'var(--mist)' }}>
          {desc}
        </span>
      )}
      {transcript.agent_hex_id && (
        // Deep link into the /a/ subagent drill-in route (SubagentPage). Carries ?projects=
        // (Task 9) — writeProjects deletes the param when empty, so the bare-path case is
        // byte-identical to before this task.
        <Link
          to={{
            pathname: `/s/${sessionUuid}/a/${transcript.agent_hex_id}`,
            search: writeProjects(new URLSearchParams(), projects).toString(),
          }}
          style={{ color: 'var(--dragonfly)', textDecoration: 'none' }}
        >
          view transcript →
        </Link>
      )}
    </div>
  )
}

/** Truncate to at most `max` characters, spending the final slot on an ellipsis when cut. */
function truncate(s: string | null, max: number): string | null {
  if (!s) return null
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}
