import type { InfiniteData, QueryClient } from "@tanstack/react-query"

import type { OpenPullRequestsPayload } from "@/lib/api"

type PullRequestRef = { repo: string; number: number }

export function forgetPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: PullRequestRef
) {
  queryClient.setQueryData(["my-pr-details", login, pr.repo, pr.number], null)
  queryClient.setQueriesData<InfiniteData<OpenPullRequestsPayload>>(
    { queryKey: ["my-pull-requests", login] },
    (data) =>
      data
        ? {
            ...data,
            pages: data.pages.map((loaded) => ({
              ...loaded,
              pullRequests: loaded.pullRequests.filter(
                (row) => row.repo !== pr.repo || row.number !== pr.number
              ),
            })),
          }
        : data
  )
}

export function refreshPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: PullRequestRef
) {
  void queryClient.invalidateQueries({
    queryKey: ["my-pr-details", login, pr.repo, pr.number],
  })
}
