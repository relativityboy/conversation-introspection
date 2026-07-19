import type { CSSProperties } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { useImportRun, useStatus, useTriggerImport } from '../api/hooks'
import type { StatusOut } from '../api/types'

const MESSAGE_MS = 4000

// The transient states an import run walks through after the ghost button is clicked. `runId`
// only exists in the 'running' phase — the useImportRun poll is keyed off it (see `phase.kind ===
// 'running' ? phase.runId : null` below), so union-typing it here (rather than a separate
// `runId: number | null` field) makes "polling active" and "have a run id" the same fact.
type Phase =
  | { kind: 'idle' }
  | { kind: 'running'; runId: number }
  | { kind: 'success' }
  | { kind: 'already-running' }
  | { kind: 'error' }

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
// route (App.tsx). Reads `useStatus()` (existing 30s-poll hook); the import button drives its own
// short-lived local Phase state layered on top of two mutually-exclusive-in-time query hooks
// (useTriggerImport, useImportRun) — see Phase's doc comment for why runId lives inside it.
export function StatusBar() {
  const statusQuery = useStatus()
  const trigger = useTriggerImport()
  const queryClient = useQueryClient()

  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  // Guards the terminal-state effect below against firing twice for the same run (StrictMode's
  // double effect invocation in dev, or a stale closure re-running before `phase` catches up).
  const handledRunId = useRef<number | null>(null)

  const runQuery = useImportRun(phase.kind === 'running' ? phase.runId : null)

  // Terminal transition: once the polled run leaves 'running', fold it into a Phase (ok →
  // success, anything else → error) and invalidate status+sessions — a completed run may have
  // added records/sessions/anomalies that both queries' cached data no longer reflects.
  //
  // A settled poll ERROR is terminal too — without this the machine wedges in 'running' with the
  // button hidden and no recovery: (a) the FIRST poll failing leaves data undefined forever;
  // (b) the server dying mid-run leaves STALE data (status 'running') alongside isError. Either
  // way: 'error' phase ("import failed", 4s → idle) and invalidate as usual — the run may well
  // have completed server-side, so a refetched status is more truthful than the cache.
  useEffect(() => {
    if (phase.kind !== 'running') return
    const run = runQuery.data
    const failed = runQuery.isError
    if (!failed && (!run || run.status === 'running')) return
    if (handledRunId.current === phase.runId) return
    handledRunId.current = phase.runId

    queryClient.invalidateQueries({ queryKey: ['status'] })
    queryClient.invalidateQueries({ queryKey: ['sessions'] })
    setPhase(!failed && run?.status === 'ok' ? { kind: 'success' } : { kind: 'error' })
  }, [phase, runQuery.data, runQuery.isError, queryClient])

  // Transient messages (everything but idle/running) self-revert after MESSAGE_MS.
  useEffect(() => {
    if (phase.kind === 'idle' || phase.kind === 'running') return
    const timer = setTimeout(() => setPhase({ kind: 'idle' }), MESSAGE_MS)
    return () => clearTimeout(timer)
  }, [phase])

  function handleImportClick() {
    trigger.mutate(undefined, {
      onSuccess: (data) => {
        handledRunId.current = null
        setPhase({ kind: 'running', runId: data.run_id })
      },
      onError: (error) => {
        setPhase(
          error instanceof ApiError && error.status === 409
            ? { kind: 'already-running' }
            : { kind: 'error' },
        )
      },
    })
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
        {phase.kind === 'idle' && (
          <button type="button" onClick={handleImportClick} style={GHOST_BTN}>
            ● import
          </button>
        )}
        {phase.kind === 'running' && (
          <span style={{ color: 'var(--dragonfly)' }}>importing…</span>
        )}
        {phase.kind === 'success' && <span>imported ✓</span>}
        {phase.kind === 'already-running' && <span>already running</span>}
        {phase.kind === 'error' && <span style={{ color: 'var(--ember)' }}>import failed</span>}
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
