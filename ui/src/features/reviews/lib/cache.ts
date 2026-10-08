import type { QueryClient } from "@tanstack/react-query"

import { api, type OpenPullRequest } from "@/lib/api"
import { BROWSER_CACHE_MAX_AGE_MS, expiresInBrowser } from "@/lib/query"

type PullRequestRef = { repo: string; number: number }

export function pullRequestPreviewQuery(pr: PullRequestRef) {
  const [owner = "", name = ""] = pr.repo.split("/")
  return {
    queryKey: ["pr-preview", owner, name, pr.number],
    queryFn: () => api.getPullRequestPreview(owner, name, pr.number),
    ...expiresInBrowser,
    gcTime: BROWSER_CACHE_MAX_AGE_MS,
  } as const
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

/**
 * Matches every login's entry because the mark-ready button is not given one.
 * Returns the undo, which puts the draft flag back.
 */
export function markPullRequestReady(
  queryClient: QueryClient,
  pr: PullRequestRef
): () => void {
  const setDraft = (draft: boolean) =>
    queryClient.setQueriesData<OpenPullRequest | null>(
      {
        predicate: ({ queryKey: [scope, , repo, number] }) =>
          scope === "my-pr-details" && repo === pr.repo && number === pr.number,
      },
      (old) => (old ? { ...old, draft } : old)
    )
  setDraft(false)
  return () => setDraft(true)
}
