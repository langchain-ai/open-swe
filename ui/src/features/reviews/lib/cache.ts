import type { QueryClient } from "@tanstack/react-query"

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
