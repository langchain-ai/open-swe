import { useQueries } from "@tanstack/react-query"

import { api, type OpenPullRequest } from "@/lib/api"

/**
 * Fill in the lightweight listing rows with their per-PR detail reads.
 *
 * `requested` is the window actually on screen, so only those rows cost a
 * request; a row whose detail read comes back null has been closed or merged
 * elsewhere and drops out of the list entirely.
 */
export function usePullRequestDetails(
  login: string,
  rows: OpenPullRequest[],
  requested: OpenPullRequest[]
) {
  const detailQueries = useQueries({
    queries: requested.map((pr) => ({
      queryKey: ["my-pr-details", login, pr.repo, pr.number],
      queryFn: () => api.myPullRequestDetails(pr.repo, pr.number),
      enabled: pr.detailsLoading === true,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
    })),
  })
  const all = rows.flatMap((pr) => {
    const index = requested.findIndex(
      (row) => row.repo === pr.repo && row.number === pr.number
    )
    const detail = detailQueries[index]
    if (detail?.data === null) return []
    if (detail?.data)
      return [
        {
          ...pr,
          ...detail.data,
          detailsLoading: false,
          detailsError: false,
          title: detail.data.title || pr.title,
          createdAt: detail.data.createdAt || pr.createdAt,
          updatedAt: detail.data.updatedAt || pr.updatedAt,
        },
      ]
    return [
      {
        ...pr,
        detailsLoading: pr.detailsLoading && !detail?.isError,
        detailsError: detail?.isError,
      },
    ]
  })
  return {
    all,
    detailsLoading: detailQueries.some((detail) => detail.isFetching),
  }
}
