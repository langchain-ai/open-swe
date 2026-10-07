import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  DesktopLocalDiff,
  DesktopLocalThread,
  DesktopProjectRef,
} from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"

const NO_LOCAL_THREADS: Array<DesktopLocalThread> = []

const NO_DIFF: DesktopLocalDiff = {
  status: "missing",
  truncated: false,
  files: [],
}

export const localThreadKeys = {
  all: ["local-threads"] as const,
  diff: (threadId: string) => ["local-thread-diff", threadId] as const,
  prDiff: (threadId: string) => ["local-thread-pr-diff", threadId] as const,
  pr: (threadId: string) => ["local-thread-pr", threadId] as const,
  repoDiff: (cwd: string) => ["local-repo-diff", cwd] as const,
  refs: (cwd: string | undefined) => ["local-repo-refs", cwd ?? ""] as const,
}

/** Worktree changes for a repository, for screens without a thread yet. */
export function useRepoDiff(cwd: string, enabled: boolean) {
  return useQuery({
    queryKey: localThreadKeys.repoDiff(cwd),
    queryFn: () => window.openSweDesktop?.getProjectDiff(cwd) ?? NO_DIFF,
    enabled: enabled && Boolean(cwd),
  })
}

const NO_REFS: Array<DesktopProjectRef> = []

export function useLocalRepoRefs(cwd: string | undefined) {
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

/** Whether the desktop app on some Mac serves this cloud thread's checkout. */
export function runsOnAMac(thread: Pick<AgentThread, "sandboxBridgeClient">) {
  return thread.sandboxBridgeClient === "desktop"
}

/**
 * The "This Mac" threads whose checkouts are on this machine. Only this app
 * changes the list, so it is refreshed where it does rather than polled.
 */
export function useLocalThreads() {
  const queryClient = useQueryClient()
  useEffect(
    () =>
      window.openSweDesktop?.onLocalThreadsChanged(
        () =>
          void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
      ),
    [queryClient]
  )
  return useQuery({
    queryKey: localThreadKeys.all,
    queryFn: async () =>
      (await window.openSweDesktop?.listLocalThreads()) ?? NO_LOCAL_THREADS,
    enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
    staleTime: Infinity,
  })
}

/** This Mac's record of the thread, or null when it runs anywhere else. */
export function useLocalThread(threadId: string): DesktopLocalThread | null {
  return (
    useLocalThreads().data?.find((thread) => thread.id === threadId) ?? null
  )
}

export function useRefreshLocalThreads() {
  const queryClient = useQueryClient()
  return () =>
    void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
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
 * the repository shares.
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

/** The checked-out branch's pull request, for the composer PR link. */
export function useLocalThreadPr(threadId: string, enabled = true) {
  return useQuery({
    queryKey: localThreadKeys.pr(threadId),
    queryFn: async () =>
      (await window.openSweDesktop?.getLocalPr(threadId)) ?? null,
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: "always",
  })
}
