import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"

import type { DesktopLocalActivity, DesktopLocalDiff } from "@/desktop"

const NO_ACTIVITY: DesktopLocalActivity = {}
const NO_DIFF: DesktopLocalDiff = { status: "missing", truncated: false, files: [] }

export const desktopExecutionKeys = {
  activity: ["desktop-execution", "activity"] as const,
  diff: (threadId: string) => ["desktop-execution", threadId, "diff"] as const,
  branchDiff: (threadId: string) =>
    ["desktop-execution", threadId, "branch-diff"] as const,
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
      return cause instanceof Error ? cause.message : "OpenAI sign-in failed"
    }
  }
  return credential.variable
    ? `Set ${credential.variable} in the environment before starting Open SWE.`
    : "Sign in to use the selected model."
}

export function useLocalThreadActivity(): DesktopLocalActivity {
  return (
    useQuery({
      queryKey: desktopExecutionKeys.activity,
      queryFn: () => window.openSweDesktop?.localActivity() ?? NO_ACTIVITY,
      enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
      refetchInterval: 1000,
    }).data ?? NO_ACTIVITY
  )
}

function useDesktopDiff(
  threadId: string,
  enabled: boolean,
  isRunning: boolean,
  branch: boolean
) {
  const query = useQuery({
    queryKey: branch
      ? desktopExecutionKeys.branchDiff(threadId)
      : desktopExecutionKeys.diff(threadId),
    queryFn: () =>
      branch
        ? window.openSweDesktop?.getLocalPrDiff(threadId) ?? NO_DIFF
        : window.openSweDesktop?.getLocalDiff(threadId) ?? NO_DIFF,
    enabled,
    refetchInterval: isRunning ? 5000 : false,
  })
  const { refetch } = query
  useEffect(() => {
    if (enabled && !isRunning) void refetch()
  }, [enabled, isRunning, refetch])
  return query
}

export const useLocalThreadDiff = (
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) => useDesktopDiff(threadId, enabled, isRunning, false)

export const useLocalThreadPrDiff = (
  threadId: string,
  enabled: boolean,
  isRunning: boolean
) => useDesktopDiff(threadId, enabled, isRunning, true)
