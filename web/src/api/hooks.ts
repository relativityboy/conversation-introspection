/**
 * react-query hooks over `./client`. `makeQueryClient()` is the single place the
 * application-wide defaults live: archive data changes slowly (only on explicit import/reparse
 * runs), so a 30s `staleTime` and no window-focus refetch avoid needless traffic.
 */

import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteFavorite,
  fetchImportRun,
  fetchMessages,
  fetchSearch,
  fetchSession,
  fetchSessions,
  fetchStatus,
  putFavorite,
  triggerImport,
  type MessagesOptions,
  type SessionFilters,
} from './client'
import type { SearchScope } from './types'

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  })
}

// --- query keys -----------------------------------------------------------------------------

const sessionsKey = (filters: SessionFilters) => ['sessions', filters] as const
const sessionKey = (uuid: string) => ['sessions', uuid] as const
const messagesKey = (transcriptId: number, opts: MessagesOptions) =>
  ['messages', transcriptId, opts] as const
const searchKey = (q: string, scope: SearchScope, sessionUuid: string | undefined) =>
  ['search', q, scope, sessionUuid] as const
const statusKey = ['status'] as const
const importRunKey = (id: number) => ['importRuns', id] as const

// --- reads ------------------------------------------------------------------------------

export function useSessions(filters: SessionFilters = {}) {
  return useQuery({
    queryKey: sessionsKey(filters),
    queryFn: () => fetchSessions(filters),
  })
}

export function useSession(uuid: string) {
  return useQuery({
    queryKey: sessionKey(uuid),
    queryFn: () => fetchSession(uuid),
    // Same policy as useMessages: an unknown session uuid is a 404 not-found, and retrying a
    // not-found only delays the pages' not-found notice. Non-404s keep the default 3 retries.
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  })
}

export function useMessages(transcriptId: number, opts: MessagesOptions = {}) {
  return useQuery({
    queryKey: messagesKey(transcriptId, opts),
    queryFn: () => fetchMessages(transcriptId, opts),
    // An `around` deep-link to a record that isn't in this transcript is a 404 (a not-found,
    // not a connectivity failure). Retrying it is a pointless storm that only delays the
    // reader's recovery notice, so skip retries for 404s; keep the default 3 for the rest.
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  })
}

export function useSearch(q: string, scope: SearchScope, sessionUuid?: string) {
  return useQuery({
    queryKey: searchKey(q, scope, sessionUuid),
    queryFn: () => fetchSearch(q, scope, sessionUuid),
    enabled: q.trim().length > 0,
  })
}

export function useStatus() {
  return useQuery({
    queryKey: statusKey,
    queryFn: fetchStatus,
    refetchInterval: 30_000,
  })
}

// --- mutations --------------------------------------------------------------------------

export function useFavorite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ uuid, favorite }: { uuid: string; favorite: boolean }) =>
      favorite ? putFavorite(uuid) : deleteFavorite(uuid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
  })
}

export function useTriggerImport() {
  return useMutation({
    mutationFn: triggerImport,
  })
}

/** Polls every 1s while the run's `status === 'running'`; pass `null` to skip fetching entirely. */
export function useImportRun(id: number | null) {
  return useQuery({
    queryKey: importRunKey(id ?? -1),
    queryFn: () => fetchImportRun(id as number),
    enabled: id !== null,
    // Stop on query error too, not just on a terminal run status: a failed refetch keeps the
    // STALE data (status still 'running'), so without the error check a mid-run server death
    // would poll a dead server every 1s forever.
    refetchInterval: (query) =>
      query.state.status !== 'error' && query.state.data?.status === 'running' ? 1000 : false,
  })
}
