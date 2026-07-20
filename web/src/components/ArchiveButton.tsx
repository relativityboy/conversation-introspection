import type { CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { useArchiveSession } from '../api/hooks'

// A quiet mist affordance for the header meta row, sized to sit beside the ↓ .jsonl link and the
// conversation-only toggle. Bare text (no border chrome) so it reads as the calmest control in the
// row -- archiving is a deliberate, low-frequency act, not a primary CTA. No confirm dialog: the
// action is reversible out-of-band (`introspect unarchive`), per spec §15.1.
const STYLE: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  letterSpacing: '.04em',
  lineHeight: 1.2,
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: 'var(--mist)',
}

export interface ArchiveButtonProps {
  sessionUuid: string
  /** The search string to carry onto the home navigation on success — the active `?projects=`
   * filter (F3 blemish fix). Home is a DIRECT link, not routed through App.tsx's catch-all
   * redirect, so it must carry the filter itself, mirroring SessionPage's not-found back-link. */
  backSearch?: string
}

/** The §15.1 archive affordance. On a 204 the session vanishes from every read surface, so the
 * reader navigates home ('/', preserving the project filter) and the hook invalidates
 * ['sessions']+['search']; a failed request leaves the user on the conversation (navigation
 * happens ONLY in the mutate onSuccess). */
export function ArchiveButton({ sessionUuid, backSearch = '' }: ArchiveButtonProps) {
  const navigate = useNavigate()
  const mutation = useArchiveSession()

  return (
    <button
      type="button"
      className="archive-button mono"
      disabled={mutation.isPending}
      onClick={() =>
        mutation.mutate(sessionUuid, {
          onSuccess: () => navigate({ pathname: '/', search: backSearch }),
        })
      }
      style={STYLE}
    >
      archive
    </button>
  )
}
