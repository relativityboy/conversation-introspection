import { createContext, useContext } from 'react'
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
