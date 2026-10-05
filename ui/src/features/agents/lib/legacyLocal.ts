import { useQuery, useQueryClient } from "@tanstack/react-query"

import type {
  DesktopLegacyLocalActivity,
  DesktopLegacyLocalThread,
} from "@/desktop"

/**
 * Threads the desktop app's retired local LangGraph server ran. They stay
 * readable and runnable on that server until it is removed; new "This Mac"
 * threads run on the cloud agent through a bridge instead.
 */

const NO_ACTIVITY: DesktopLegacyLocalActivity = {}

export const legacyLocalThreadKeys = {
  all: ["legacy-local-threads"] as const,
  activity: ["legacy-local-thread-activity"] as const,
  detail: (threadId: string) => ["legacy-local-threads", threadId] as const,
  ready: (threadId: string) => ["legacy-local-thread-ready", threadId] as const,
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

export function useReadyLegacyLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: legacyLocalThreadKeys.ready(threadId),
    enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
    queryFn: async () => {
      const thread =
        (await window.openSweDesktop?.getLegacyLocalThread(threadId)) ?? null
      if (thread)
        queryClient.setQueryData(legacyLocalThreadKeys.detail(threadId), thread)
      return thread
    },
    refetchOnMount: "always",
  })
}

export function useLegacyLocalThread(threadId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: legacyLocalThreadKeys.detail(threadId),
    queryFn: () =>
      window.openSweDesktop?.getLegacyLocalThread(threadId) ?? null,
    initialData: () =>
      queryClient
        .getQueryData<Array<DesktopLegacyLocalThread>>(
          legacyLocalThreadKeys.all
        )
        ?.find((thread) => thread.id === threadId),
    initialDataUpdatedAt: 0,
  })
}

export function useLegacyLocalThreads(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: legacyLocalThreadKeys.all,
    queryFn: () => window.openSweDesktop?.listLegacyLocalThreads() ?? [],
    enabled: options.enabled,
    refetchInterval: options.enabled === false ? false : 1000,
  })
}

export function useLegacyLocalActivity(): DesktopLegacyLocalActivity {
  return (
    useQuery({
      queryKey: legacyLocalThreadKeys.activity,
      queryFn: () =>
        window.openSweDesktop?.legacyLocalActivity() ?? NO_ACTIVITY,
      enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
      refetchInterval: 1000,
    }).data ?? NO_ACTIVITY
  )
}

/**
 * Clear a local thread's unread dot on click. Opening the thread is what marks
 * it viewed for real; this only covers the poll interval until that lands.
 */
export function useMarkLegacyLocalThreadViewed() {
  const queryClient = useQueryClient()
  return (threadId: string) =>
    queryClient.setQueryData<Array<DesktopLegacyLocalThread>>(
      legacyLocalThreadKeys.all,
      (prev) =>
        prev?.map((thread) =>
          thread.id === threadId ? { ...thread, viewed: true } : thread
        )
    )
}

export function useRefreshLegacyLocalThreads() {
  const queryClient = useQueryClient()
  return (threadId?: string) => {
    void queryClient.invalidateQueries({
      queryKey: legacyLocalThreadKeys.all,
    })
    if (threadId) {
      void queryClient.invalidateQueries({
        queryKey: legacyLocalThreadKeys.detail(threadId),
      })
    }
  }
}
