import { useQuery } from "@tanstack/react-query"

import type { PullRequestCheckState } from "./api"
import type { SidebarThreadItem } from "./sidebarThreads"
import { agentsApi } from "./api"

/** Cap matches the server's, so the request never silently drops the tail. */
const MAX_TRACKED = 50

export function useSidebarPullRequestChecks(
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

  const query = useQuery({
    queryKey: ["agent-threads", "pull-request-checks", keys],
    queryFn: () => agentsApi.getPullRequestChecks(refs.map(([, ref]) => ref)),
    enabled: enabled && refs.length > 0,
    staleTime: 60_000,
    refetchInterval: 60_000,
    // Keeps dots on screen while a refetch for a changed key set is in flight.
    placeholderData: (previous) => previous,
    retry: false,
  })

  return (item: SidebarThreadItem): PullRequestCheckState | undefined =>
    item.prRef
      ? query.data?.[`${item.prRef.repoFullName}#${item.prRef.number}`]
      : undefined
}
