import { useQueries, useQuery } from "@tanstack/react-query"

import type { PullRequestLiveState, PullRequestSnapshot } from "./api"
import type { AgentPullRequestStatusResponse } from "./types"
import type { SidebarThreadItem } from "./sidebarThreads"
import { agentsApi } from "./api"
import { agentThreadKeys } from "./queries"
import { invalidationTopic } from "@/lib/invalidations/topics"

/** Cap matches the server's, so the request never silently drops the tail. */
const MAX_TRACKED = 50

function liveState(
  status: AgentPullRequestStatusResponse | undefined,
  repoFullName: string,
  number: number
): PullRequestLiveState | null {
  const pr = status?.pullRequests.find(
    (entry) => entry.repoFullName === repoFullName && entry.number === number
  )
  if (!pr?.statusAvailable || !pr.state) return null
  return pr.state === "open" && pr.isDraft ? "draft" : pr.state
}

/**
 * Live pull-request state for sidebar rows. Thread metadata only records the
 * state a PR had when it was opened, so a merged PR would otherwise keep
 * rendering as open.
 *
 * Two sources, freshest first: the per-thread status the thread view already
 * fetched (read from cache, never re-requested — this is what makes an open
 * thread's row flip at the same moment its status bar does), then the batched
 * endpoint that covers every other row in one request.
 */
export function useSidebarPullRequests(
  items: ReadonlyArray<SidebarThreadItem>,
  enabled: boolean
) {
  const refs = [
    ...new Map(
      items
        .filter((item) => item.prRef && item.pr?.state !== "merged")
        .map((item) => [
          `${item.prRef!.repoFullName}#${item.prRef!.number}`,
          item.prRef!,
        ])
    ),
  ].slice(0, MAX_TRACKED)
  const keys = refs.map(([key]) => key).sort()

  const batch = useQuery({
    queryKey: ["agent-threads", "pull-request-checks", keys],
    queryFn: () => agentsApi.getPullRequestChecks(refs.map(([, ref]) => ref)),
    enabled: enabled && refs.length > 0,
    staleTime: 60_000,
    meta: {
      invalidatedBy: refs.map(([, ref]) =>
        invalidationTopic(
          "pull-requests",
          `${ref.repoFullName}/${ref.number}`.toLowerCase()
        )
      ),
    },
    // Keeps dots on screen while a refetch for a changed key set is in flight.
    placeholderData: (previous) => previous,
    retry: false,
  })

  // Cache-only subscriptions: `enabled: false` issues no request but still
  // re-renders when the thread view's own status query writes a result.
  const cloudIds = items.filter((item) => item.prRef).map((item) => item.id)
  const perThread = useQueries({
    queries: cloudIds.map((threadId) => ({
      queryKey: agentThreadKeys.pullRequestStatus(threadId),
      meta: {
        invalidatedBy: [
          invalidationTopic("threads", threadId),
          invalidationTopic("pull-requests"),
        ],
      },
      enabled: false,
      staleTime: Infinity,
    })),
    combine: (results) =>
      new Map(
        cloudIds.map((threadId, index) => [
          threadId,
          {
            status: results[index]?.data as
              | AgentPullRequestStatusResponse
              | undefined,
            updatedAt: results[index]?.dataUpdatedAt ?? 0,
          },
        ])
      ),
  })

  return (item: SidebarThreadItem): PullRequestSnapshot | undefined => {
    if (!item.prRef) return undefined
    const { repoFullName, number } = item.prRef
    const batched = batch.data?.[`${repoFullName}#${number}`]
    const cached = perThread.get(item.id)
    const fresh =
      cached && cached.updatedAt >= batch.dataUpdatedAt
        ? liveState(cached.status, repoFullName, number)
        : null
    if (!batched) return fresh ? { checks: "unknown", state: fresh } : undefined
    return fresh ? { ...batched, state: fresh } : batched
  }
}
