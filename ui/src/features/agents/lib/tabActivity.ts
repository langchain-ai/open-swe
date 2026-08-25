import { useCallback, useMemo } from "react"
import { useNavigate } from "@tanstack/react-router"

import { cloudTabId, localTabId } from "@/features/agents/lib/tabs"
import {
  useSeedAgentThreadDetails,
  useSidebarThreads,
} from "@/features/agents/lib/queries"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
} from "@/features/agents/lib/desktopLocal"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"

const NO_LOCAL_THREADS: Array<never> = []

/** What a tab shows next to its title: a spinner, or a dot for unseen results. */
export type TabActivity = "running" | "attention"

/**
 * The shell's subscription to every session the user has, cloud and local. It
 * badges the tab strip and fires run-completion notifications, so both keep
 * working while the home screen is closed. The queries are the same ones the
 * home screen reads, so this shares their cache rather than doubling requests.
 */
export function useTabActivity({
  activeThreadId,
  cloudEnabled,
}: {
  activeThreadId?: string
  cloudEnabled: boolean
}): Record<string, TabActivity> {
  const navigate = useNavigate()
  const cloud = useSidebarThreads({ activeThreadId, enabled: cloudEnabled })
  const cloudThreads = cloud.data.active.items
  const localThreads = useDesktopLocalThreads().data ?? NO_LOCAL_THREADS
  const localActivity = useLocalThreadActivity()

  const openThread = useCallback(
    (threadId: string) =>
      void navigate({ to: "/agents/$threadId", params: { threadId } }),
    [navigate]
  )
  useSeedAgentThreadDetails(cloudThreads, activeThreadId)
  useRunCompletionNotifier(cloudThreads, activeThreadId, openThread)

  return useMemo(() => {
    const map: Record<string, TabActivity> = {}
    for (const thread of cloudThreads) {
      if (thread.status === "running") map[cloudTabId(thread.id)] = "running"
      else if (thread.status === "finished" && !thread.viewed) {
        map[cloudTabId(thread.id)] = "attention"
      }
    }
    for (const thread of localThreads) {
      if (localActivity[thread.id] === "running") {
        map[localTabId(thread.id)] = "running"
      } else if (!thread.viewed) map[localTabId(thread.id)] = "attention"
    }
    return map
  }, [cloudThreads, localActivity, localThreads])
}
