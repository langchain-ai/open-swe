import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import type { DesktopLocalActivity } from "@/desktop"
import type {LocalThread} from "@/features/agents/lib/localQueries";
import {
  
  localActivityQuery,
  localBranchDiffQuery,
  localDiffQuery,
  localKeys,
  localThreadQuery,
  localThreadsQuery
} from "@/features/agents/lib/localQueries"
import { getLocalThread } from "@/features/agents/lib/localFunctions"
import { isLocalRuntime } from "@/lib/desktop-local-mode"

const NO_ACTIVITY: DesktopLocalActivity = {}

export async function ensureDesktopModelCredential(
  modelId?: string
): Promise<string | null> {
  const desktop = window.openSweDesktop
  if (!desktop) return null
  const credential = await desktop.localModelCredentialStatus(modelId)
  if (credential.available) return null
  if (credential.canSignIn) {
    try {
      const result = await desktop.signInLocalOpenAI()
      if (result.signedIn) return null
    } catch (cause) {
      return cause instanceof Error ? cause.message : "OpenAI sign-in failed"
    }
  }
  return credential.variable
    ? `Set ${credential.variable} in the environment before starting Open SWE.`
    : "Sign in to use the selected model."
}

export function useReadyDesktopLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: localKeys.threadReady(threadId),
    enabled: isLocalRuntime(),
    queryFn: async () => {
      const thread = await getLocalThread({ data: threadId })
      if (thread) queryClient.setQueryData(localKeys.thread(threadId), thread)
      return thread
    },
    refetchOnMount: "always",
  })
}

export function useDesktopLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    ...localThreadQuery(threadId),
    initialData: () =>
      queryClient
        .getQueryData<Array<LocalThread>>(localKeys.threads)
        ?.find((thread) => thread.id === threadId),
    initialDataUpdatedAt: 0,
  })
}

export function useDesktopLocalThreads(options: { enabled?: boolean } = {}) {
  return useQuery(localThreadsQuery(options.enabled))
}

export function useLocalThreadActivity(): DesktopLocalActivity {
  return useQuery(localActivityQuery()).data ?? NO_ACTIVITY
}

/** A settled thread stops polling, so its final diff needs one explicit refetch. */
function useSettledRefetch(
  query: { refetch: () => unknown },
  enabled: boolean,
  isRunning: boolean
) {
  const { refetch } = query
  useEffect(() => {
    if (enabled && !isRunning) void refetch()
  }, [enabled, isRunning, refetch])
}

export function useLocalThreadDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery(localDiffQuery(threadId, enabled, isRunning))
  useSettledRefetch(query, enabled, isRunning)
  return query
}

export function useLocalThreadPrDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery(localBranchDiffQuery(threadId, enabled, isRunning))
  useSettledRefetch(query, enabled, isRunning)
  return query
}

export function useRefreshLocalThreads() {
  const queryClient = useQueryClient()
  return (threadId?: string) => {
    void queryClient.invalidateQueries({ queryKey: localKeys.threads })
    if (threadId) {
      void queryClient.invalidateQueries({
        queryKey: localKeys.thread(threadId),
      })
    }
  }
}
