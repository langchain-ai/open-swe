import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  DesktopLocalActivity,
  DesktopLocalDiff,
  DesktopLocalThreadSummary,
} from "@/desktop"

const NO_ACTIVITY: DesktopLocalActivity = {}

const NO_DIFF: DesktopLocalDiff = {
  status: "missing",
  truncated: false,
  files: [],
}

export const localThreadKeys = {
  all: ["local-threads"] as const,
  activity: ["local-thread-activity"] as const,
  detail: (threadId: string) => ["local-threads", threadId] as const,
  ready: (threadId: string) => ["local-thread-ready", threadId] as const,
  diff: (threadId: string) => ["local-thread-diff", threadId] as const,
}

export function useReadyDesktopLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: localThreadKeys.ready(threadId),
    enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
    queryFn: async () => {
      const thread =
        (await window.openSweDesktop?.getLocalThread(threadId)) ?? null
      if (thread)
        queryClient.setQueryData(localThreadKeys.detail(threadId), thread)
      return thread
    },
    refetchOnMount: "always",
  })
}

export function useDesktopLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: localThreadKeys.detail(threadId),
    queryFn: () => window.openSweDesktop?.getLocalThread(threadId) ?? null,
    initialData: () =>
      queryClient
        .getQueryData<Array<DesktopLocalThreadSummary>>(localThreadKeys.all)
        ?.find((thread) => thread.id === threadId),
    initialDataUpdatedAt: 0,
  })
}

export function useDesktopLocalThreads(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: localThreadKeys.all,
    queryFn: () => window.openSweDesktop?.listLocalThreads() ?? [],
    enabled: options.enabled,
    refetchInterval: options.enabled === false ? false : 1000,
  })
}

export function useLocalThreadActivity(): DesktopLocalActivity {
  return (
    useQuery({
      queryKey: localThreadKeys.activity,
      queryFn: () => window.openSweDesktop?.localActivity() ?? NO_ACTIVITY,
      enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
      refetchInterval: 1000,
    }).data ?? NO_ACTIVITY
  )
}

export function useLocalThreadDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery({
    queryKey: localThreadKeys.diff(threadId),
    queryFn: () => window.openSweDesktop?.getLocalDiff(threadId) ?? NO_DIFF,
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
  return (threadId?: string) => {
    void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
    if (threadId) {
      void queryClient.invalidateQueries({
        queryKey: localThreadKeys.detail(threadId),
      })
    }
  }
}
