import type { QueryClient } from "@tanstack/react-query"

import type { OpenPullRequest } from "@/lib/api"

type PullRequestRef = { repo: string; number: number }

export function refreshPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: PullRequestRef
) {
  void queryClient.invalidateQueries({
    queryKey: ["my-pr-details", login, pr.repo, pr.number],
  })
}

/** Matches every login's entry because the mark-ready button is not given one. */
export function markPullRequestReady(
  queryClient: QueryClient,
  pr: PullRequestRef
) {
  queryClient.setQueriesData<OpenPullRequest | null>(
    {
      predicate: ({ queryKey: [scope, , repo, number] }) =>
        scope === "my-pr-details" && repo === pr.repo && number === pr.number,
    },
    (old) => (old ? { ...old, draft: false } : old)
  )
}
