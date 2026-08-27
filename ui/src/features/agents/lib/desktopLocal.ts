import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import type { DesktopLocalDiff } from "@/desktop"

const NO_DIFF: DesktopLocalDiff = {
  status: "missing",
  truncated: false,
  files: [],
}

export const localThreadKeys = {
  all: ["local-threads"] as const,
  diff: (threadId: string) => ["local-thread-diff", threadId] as const,
  prDiff: (threadId: string) => ["local-thread-pr-diff", threadId] as const,
}

export function useLocalThreadDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery({
    queryKey: localThreadKeys.diff(threadId),
    queryFn: async () =>
      (await window.openSweDesktop?.getLocalDiff(threadId)) ?? NO_DIFF,
    enabled,
    refetchInterval: isRunning ? 5000 : false,
  })

  const { refetch } = query
  useEffect(() => {
    if (enabled && !isRunning) void refetch()
  }, [enabled, isRunning, refetch])

  return query
}

/**
 * What the thread's branch has committed on top of its pull request's base.
 * Unlike the checkpoint diff this ignores the worktree, which every session in
 * the project shares.
 */
export function useLocalThreadPrDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery({
    queryKey: localThreadKeys.prDiff(threadId),
    queryFn: async () =>
      (await window.openSweDesktop?.getLocalPrDiff(threadId)) ?? NO_DIFF,
    enabled,
    refetchInterval: isRunning ? 5000 : false,
  })

  const { refetch } = query
  useEffect(() => {
    if (enabled && !isRunning) void refetch()
  }, [enabled, isRunning, refetch])

  return query
}

export function useRefreshLocalThreads() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
  }
}
