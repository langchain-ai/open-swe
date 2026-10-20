import { useSyncExternalStore } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { agentThreadKeys } from "@/features/agents/lib/queries"
import { reportError } from "@/lib/errorReporting"
import { useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { TranscriptUpgradeBusy, upgradeTranscript } from "./api"
import type { TranscriptUpgradeProgress } from "./api"
import type { AgentThread } from "@/features/agents/lib/types"

export type TranscriptUpgradeState =
  /** Settings are still loading, so it is not yet known which reader to mount. */
  | { kind: "pending" }
  /** Nothing to do, or the upgrade failed: read the thread the way it is stamped. */
  | { kind: "settled" }
  | { kind: "upgrading"; progress: TranscriptUpgradeProgress | null }

const progressByThread = new Map<string, TranscriptUpgradeProgress>()
const progressListeners = new Set<() => void>()

function publishProgress(
  threadId: string,
  progress: TranscriptUpgradeProgress
): void {
  progressByThread.set(threadId, progress)
  progressListeners.forEach((listener) => listener())
}

function subscribeProgress(listener: () => void): () => void {
  progressListeners.add(listener)
  return () => progressListeners.delete(listener)
}

/** The stamp changes every viewer's reader, so only the owner's opt-in upgrades. */
function isOwner(thread: AgentThread, login: string | undefined): boolean {
  const owner = thread.ownerLogin?.trim().toLowerCase()
  return Boolean(owner) && owner === login?.trim().toLowerCase()
}

/**
 * Upgrades a legacy thread to the transcript log when its owner opens it, behind the
 * `experimental_transcript_upgrade` experiment. On success the cached thread
 * is stamped `transcript: "v2"`, which is what switches the page's reader; on
 * failure the page keeps the SDK stream and an error toast says so. A busy
 * thread is left alone quietly, to be upgraded on a later open.
 *
 * One upgrade per thread per page load: it is keyed as a query, so remounts
 * share it and a failure is not retried until the page reloads.
 */
export function useTranscriptUpgrade(
  threadId: string,
  thread: AgentThread | undefined
): TranscriptUpgradeState {
  const queryClient = useQueryClient()
  const session = useSession()
  const profile = useProfile()
  const progress = useSyncExternalStore(
    subscribeProgress,
    () => progressByThread.get(threadId) ?? null
  )
  const settingsKnown =
    session.isError ||
    session.data === null ||
    (session.data !== undefined &&
      (session.data.transcript_recording !== true ||
        profile.isSuccess ||
        profile.isError))
  const wanted =
    settingsKnown &&
    session.data?.transcript_recording === true &&
    profile.data?.experimental_transcript_upgrade === true &&
    thread !== undefined &&
    isOwner(thread, session.data?.login) &&
    thread.transcript !== "v2" &&
    thread.status !== "running"
  const upgrade = useQuery({
    queryKey: ["transcriptUpgrade", threadId],
    queryFn: async () => {
      try {
        await upgradeTranscript(threadId, (update) =>
          publishProgress(threadId, update)
        )
      } catch (error) {
        if (!(error instanceof TranscriptUpgradeBusy))
          reportError({ title: "Couldn't speed up this thread", error })
        throw error
      }
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(threadId),
        (current) => (current ? { ...current, transcript: "v2" } : current)
      )
      return true
    },
    enabled: wanted,
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnReconnect: false,
  })

  if (!settingsKnown && thread?.transcript !== "v2") return { kind: "pending" }
  if (wanted && upgrade.isPending) return { kind: "upgrading", progress }
  return { kind: "settled" }
}
