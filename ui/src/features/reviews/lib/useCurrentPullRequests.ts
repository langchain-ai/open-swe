import { useQueries } from "@tanstack/react-query"

import type { OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"

import { leftOpenList, pullRequestStatusQuery } from "./cache"

/**
 * Keep the listed rows current as their pull requests change.
 *
 * Each row in `requested` subscribes to its pull request's status, seeded from
 * the listing fetched at `listedAt`, so it costs no read until that pull request
 * changes or an action updates it; a row that comes back closed or merged
 * drops out of the list, unless `kept` holds it.
 */
export function useCurrentPullRequests(
  login: string,
  rows: OpenPullRequest[],
  requested: OpenPullRequest[],
  listedAt: number,
  kept: (pr: OpenPullRequest) => boolean
) {
  const statuses = useQueries({
    queries: requested.map((pr) => ({
      ...pullRequestStatusQuery(login, pr),
      initialData: pr,
      initialDataUpdatedAt: listedAt,
      ...expiresInBrowser,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
    })),
  })
  return rows.flatMap((pr) => {
    const index = requested.findIndex(
      (row) => row.repo === pr.repo && row.number === pr.number
    )
    const status = statuses[index]
    const current = status && status.dataUpdatedAt > listedAt ? status.data : pr
    if (!leftOpenList(current)) return [current ?? pr]
    return kept(pr) ? [current ?? pr] : []
  })
}
