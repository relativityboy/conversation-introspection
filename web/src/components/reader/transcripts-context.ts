import { createContext, useContext, useMemo } from 'react'
import type { TranscriptInfo } from '../../api/types'

// NOTE(claude): the subagent JOIN lives here as context, not props. A tool_use block becomes a
// subagent dispatch when some captured transcript's parent_tool_use_id === block.tool_use_id
// (Task 0 exposed both fields). Prop-drilling the transcript inventory down through the
// virtualized MessageTurn tree would be the wrong shape — SessionPage owns SessionDetail, so it
// publishes it here and SubagentChip consumes it wherever it renders. sessionUuid rides along so
// the "view transcript →" Link can build /s/{uuid}/a/{hex} without a second lookup.
export interface TranscriptsContextValue {
  sessionUuid: string
  transcripts: TranscriptInfo[]
}

// Empty default: rendered outside a provider (isolated tests, non-session surfaces), no tool_use
// block can match, so every tool_use degrades to a plain ToolBlock. Deliberate, not a bug.
const TranscriptsContext = createContext<TranscriptsContextValue>({
  sessionUuid: '',
  transcripts: [],
})

export const TranscriptsProvider = TranscriptsContext.Provider

export function useTranscripts(): TranscriptsContextValue {
  return useContext(TranscriptsContext)
}

// The set of tool_use_ids that resolve to a captured subagent transcript -- the client mirror
// of the server's `_has_resolved_dispatch()` (routes/sessions.py `_view_filter`, final review
// C1). Threaded into `isVisibleInView` by BOTH call sites that drive row visibility/navigation
// (MessageTurn's row gate, RawRecordInspector's prev/next), so a resolved dispatch row -- one
// whose SubagentChip actually renders a "view transcript →" link -- survives a filtered view
// identically in the reader and in the modal: the SAME set, computed here once, is what keeps
// them from ever drifting on which rows that is. Memoized on `transcripts` so re-renders of a
// row deep in the tree don't rebuild the set from scratch every time.
export function useDispatchToolUseIds(): ReadonlySet<string> {
  const { transcripts } = useTranscripts()
  return useMemo(
    () =>
      new Set(
        transcripts
          .map((t) => t.parent_tool_use_id)
          .filter((id): id is string => id !== null),
      ),
    [transcripts],
  )
}
