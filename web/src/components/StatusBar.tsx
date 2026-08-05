import type { CSSProperties } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, fetchImportRun, triggerImport } from '../api/client'
import { useStatus } from '../api/hooks'
import { ActionButton } from './ActionButton'
import type { StatusOut } from '../api/types'

const BAR_STYLE: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr auto 1fr',
  alignItems: 'center',
  gap: 14,
  padding: '8px 18px',
  fontFamily: 'var(--mono)',
  fontSize: 11,
  color: 'var(--mist)',
}

const GHOST_BTN: CSSProperties = {
  fontFamily: 'var(--mono)',
  fontSize: 11,
  color: 'var(--mist)',
  background: 'transparent',
  border: '1px solid var(--shore)',
  borderRadius: 5,
  padding: '3px 12px',
  cursor: 'pointer',
}

/** 'Xm ago' below an hour, else 'Xh ago'. `null`/unparsable → 'never' (no import has landed). */
function relativeTime(iso: string | null): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'never'
  const minutes = Math.max(0, Math.floor((Date.now() - then) / 60_000))
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.floor(minutes / 60)}h ago`
}

// NOTE(claude): decimal MB (1000^2), not MiB — the label says "MB", and the mockup's static
// "47.2 MB" example is decimal for a plausible archive size. This DELIBERATELY differs from
// ToolBlock's formatBytes (binary KB for tool-output size hints); the two are separate one-off
// conventions, not a missing shared util — see the matching NOTE there.
function formatMb(bytes: number): string {
  return (bytes / 1_000_000).toFixed(1)
}

function leftText(status: StatusOut): string {
  const relative = status.last_run
    ? relativeTime(status.last_run.finished_at ?? status.last_run.started_at)
    : 'never'
  return `last import ${relative} · ${status.records} records · ${status.files} files`
}

// The waterline: import status + the manual import trigger, present in the app footer on every
// route (App.tsx). Reads `useStatus()` (existing 30s-poll hook); the import button is an
// `ActionButton` (Task 1) whose `onClick` is `runImport` below — a single async function that
// triggers the run, polls it to a terminal state, and always invalidates in a `finally` (the 409
// short-circuit returns before that block, per its own comment there).
export function StatusBar() {
  const statusQuery = useStatus()
  const queryClient = useQueryClient()

  async function runImport(): Promise<string | void> {
    let runId: number
    try {
      runId = (await triggerImport()).run_id
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) return 'already running' // benign, neutral flash
      throw new Error('import failed')
    }
    try {
      for (;;) {
        const run = await fetchImportRun(runId)
        if (run.status !== 'running') {
          if (run.status === 'ok') return 'imported ✓'
          throw new Error('import failed')
        }
        await new Promise((r) => setTimeout(r, 1000))
      }
    } catch {
      // Normalizes BOTH the run-status throw above (already 'import failed') and a raw poll
      // failure (fetchImportRun rejecting, e.g. a network error) to the same message — without
      // this, ActionButton's `title`/`aria-label` would leak the underlying fetch error instead
      // of the constraint's exact copy. The visible label is unaffected either way: ActionButton
      // always renders its own fixed `⚠ {text} failed` regardless of the thrown message.
      throw new Error('import failed')
    } finally {
      // Terminal either way (ok, failed run, dead poll): a failed poll may hide a run that
      // completed server-side, so refetched truth beats the cache — same reasoning as the old
      // Phase machine's error path. 409 returns BEFORE this block: an already-running import
      // will invalidate when ITS owner finishes; this click changed nothing.
      queryClient.invalidateQueries({ queryKey: ['status'] })
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    }
  }

  // Server down: a single calm line, nothing else — no button, no anomaly/archive figures that
  // would otherwise be stale or misleadingly blank.
  if (statusQuery.isError) {
    return (
      <div style={BAR_STYLE}>
        <span>archive offline</span>
      </div>
    )
  }

  const status = statusQuery.data

  return (
    <div style={BAR_STYLE}>
      <span>{status ? leftText(status) : '…'}</span>

      <span style={{ justifySelf: 'center' }}>
        <ActionButton glyph="●" text="import" onClick={runImport} style={GHOST_BTN} />
      </span>

      <span style={{ justifySelf: 'end', display: 'flex', gap: 18 }}>
        {status && (
          <>
            <span
              style={{
                color:
                  status.anomalies.error > 0 ? 'var(--ember)' : 'var(--mist)',
              }}
            >
              {status.anomalies.error + status.anomalies.warn + status.anomalies.info} anomalies
            </span>
            <span>archive: {formatMb(status.archive_bytes)} MB</span>
          </>
        )}
      </span>
    </div>
  )
}
