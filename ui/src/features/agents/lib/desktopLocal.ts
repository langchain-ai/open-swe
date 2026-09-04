import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  DesktopLocalActivity,
  DesktopLocalDiff,
  DesktopLocalThreadSummary,
  DesktopProjectRef,
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
  prDiff: (threadId: string) => ["local-thread-pr-diff", threadId] as const,
  refs: (cwd: string | undefined) => ["local-project-refs", cwd ?? ""] as const,
}

const NO_REFS: Array<DesktopProjectRef> = []

export function useLocalProjectRefs(cwd: string | undefined) {
  return useQuery({
    queryKey: localThreadKeys.refs(cwd),
    enabled: Boolean(cwd),
    queryFn: async () =>
      (cwd ? await window.openSweDesktop?.getProjectBranches(cwd) : null)
        ?.branches ?? NO_REFS,
    initialData: NO_REFS,
    initialDataUpdatedAt: 0,
  })
}

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
      return cause instanceof Error ? cause.message : "ChatGPT sign-in failed"
    }
  }
  return credential.variable
    ? `Set ${credential.variable} in the environment before starting Open SWE.`
    : "Sign in to use the selected model."
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
    queryFn: () => window.openSweDesktop?.getLocalPrDiff(threadId) ?? NO_DIFF,
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
 * Clear a local thread's unread dot on click. Opening the thread is what marks
 * it viewed for real; this only covers the poll interval until that lands.
 */
export function useMarkLocalThreadViewed() {
  const queryClient = useQueryClient()
  return (threadId: string) =>
    queryClient.setQueryData<Array<DesktopLocalThreadSummary>>(
      localThreadKeys.all,
      (prev) =>
        prev?.map((thread) =>
          thread.id === threadId ? { ...thread, viewed: true } : thread
        )
    )
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
