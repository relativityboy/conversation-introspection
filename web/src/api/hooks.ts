/**
 * react-query hooks over `./client`. `makeQueryClient()` is the single place the
 * application-wide defaults live: archive data changes slowly (only on explicit import/reparse
 * runs), so a 30s `staleTime` and no window-focus refetch avoid needless traffic.
 */

import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteFavorite,
  fetchMessages,
  fetchProjects,
  fetchRawRecord,
  fetchSearch,
  fetchSession,
  fetchSessions,
  fetchStatus,
  postResume,
  putArchive,
  putFavorite,
  putSessionTitle,
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
const searchKey = (
  q: string,
  scope: SearchScope,
  sessionUuid: string | undefined,
  projects: string[] | undefined,
) => ['search', q, scope, sessionUuid, projects] as const
const rawRecordKey = (uuid: string) => ['rawRecord', uuid] as const
const statusKey = ['status'] as const
const projectsKey = ['projects'] as const

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

// `projects` is threaded through as a real positional arg on every call, including session scope
// (where it's simply `undefined` — see ConversationSearchResults, which never passes it: the
// server explicitly ignores `projects=` under scope=session, so there's nothing to filter, per
// §14.2's binding note). Passing it through uniformly (rather than branching on scope here) keeps
// this hook a plain, unconditional plumb — the "don't pass projects for session scope" rule lives
// entirely at the CALL SITE (ConversationSearchResults just never supplies the 4th arg).
export function useSearch(q: string, scope: SearchScope, sessionUuid?: string, projects?: string[]) {
  return useQuery({
    queryKey: searchKey(q, scope, sessionUuid, projects),
    queryFn: () => fetchSearch(q, scope, sessionUuid, undefined, undefined, projects),
    enabled: q.trim().length > 0,
  })
}

// The raw-record inspector's fetch (§15.2). `uuid` is null while the modal is closed, so the query
// is disabled and no request fires — the hotkeys/fetch must not run when nothing is open. A 404
// (record gone, or its session archived) is a not-found, not a connectivity failure: skip the
// retry storm the same way useMessages/useSession do, so the modal shows its calm notice at once.
export function useRawRecord(uuid: string | null) {
  return useQuery({
    queryKey: rawRecordKey(uuid ?? ''),
    queryFn: () => fetchRawRecord(uuid as string),
    enabled: uuid !== null,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  })
}

export function useStatus() {
  return useQuery({
    queryKey: statusKey,
    queryFn: fetchStatus,
    refetchInterval: 30_000,
  })
}

// Projects change only on explicit import/reparse, never mid-session, so a long staleTime
// avoids refetching the list on every mount. Invalidation is hooked up at the two sites that can
// change project counts or membership: StatusBar's `runImport` (its `finally` block) and
// useArchiveSession below, both of which invalidate ['projects'] alongside their existing keys.
export function useProjects() {
  return useQuery({
    queryKey: projectsKey,
    queryFn: fetchProjects,
    staleTime: Infinity,
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

export function useSessionTitle() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ uuid, title }: { uuid: string; title: string }) =>
      putSessionTitle(uuid, title),
    onSuccess: () => {
      // ['sessions'] is a PREFIX match: it covers both the list key (['sessions', filters]) AND
      // the detail key (sessionKey above is ['sessions', uuid], not a separate ['session', uuid]
      // -- see the query-keys section). ['search'] covers searchKey groups, which embed a
      // SessionSummary (and therefore a possibly-stale title) per group -- without invalidating
      // it a renamed session would show its old title in search results until staleTime lapses
      // (plan critique F2/F6).
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      queryClient.invalidateQueries({ queryKey: ['search'] })
    },
  })
}

// Archiving hides a session from every read surface (§15.1). Invalidate the SAME two prefixes
// useSessionTitle does: ['sessions'] covers the list (the archived row must drop out) AND the
// detail key; ['search'] covers searchKey groups, which embed a SessionSummary per group that
// must no longer surface the archived session. Navigation ('/') is the caller's job (ArchiveButton
// via useNavigate) -- a mutation hook has no router, and only the caller knows where "away" is.
export function useArchiveSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (uuid: string) => putArchive(uuid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
      queryClient.invalidateQueries({ queryKey: ['search'] })
      // Archiving changes per-project counts (Task 7, spec §6.2).
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useResumeSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (uuid: string) => postResume(uuid),
    // ['sessions'] prefixes the detail key ['sessions', uuid] — a restore flips on_disk.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sessions'] }),
  })
}
