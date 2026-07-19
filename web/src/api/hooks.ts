/**
 * react-query hooks over `./client`. `makeQueryClient()` is the single place the
 * application-wide defaults live: archive data changes slowly (only on explicit import/reparse
 * runs), so a 30s `staleTime` and no window-focus refetch avoid needless traffic.
 */

import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
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
  })
}

export function useMessages(transcriptId: number, opts: MessagesOptions = {}) {
  return useQuery({
    queryKey: messagesKey(transcriptId, opts),
    queryFn: () => fetchMessages(transcriptId, opts),
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
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1000 : false),
  })
}
