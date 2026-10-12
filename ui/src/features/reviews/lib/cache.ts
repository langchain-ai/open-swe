import type { QueryClient } from "@tanstack/react-query"

import { api, type OpenPullRequest } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { BROWSER_CACHE_MAX_AGE_MS, expiresInBrowser } from "@/lib/query"

type PullRequestRef = { repo: string; number: number }

export const PULL_REQUEST_STATUS = "pull-request-status"

export function pullRequestPreviewQuery(pr: PullRequestRef) {
  const [owner = "", name = ""] = pr.repo.split("/")
  return {
    queryKey: ["pr-preview", owner, name, pr.number],
    queryFn: () => api.getPullRequestPreview(owner, name, pr.number),
    ...expiresInBrowser,
    gcTime: BROWSER_CACHE_MAX_AGE_MS,
  } as const
}

/** Invalidated whenever the pull request or its review changes; `repo` is `owner/name`. */
export function pullRequestTopic(pr: PullRequestRef) {
  return invalidationTopic(
    "pull-requests",
    `${pr.repo}/${pr.number}`.toLowerCase()
  )
}

/** A pull request's live state on GitHub, shared by every page that shows it. */
export function pullRequestStatusQuery(login: string, pr: PullRequestRef) {
  return {
    queryKey: [PULL_REQUEST_STATUS, login, pr.repo, pr.number],
    queryFn: () => api.pullRequestStatus(pr.repo, pr.number),
    meta: { invalidatedBy: [pullRequestTopic(pr)] },
  } as const
}

/** Unreadable, or closed or merged since the open list was fetched. */
export function leftOpenList(status: OpenPullRequest | null | undefined) {
  return status === null || (status !== undefined && status.state !== "open")
}

export function refreshPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: PullRequestRef
) {
  void queryClient.invalidateQueries({
    queryKey: pullRequestStatusQuery(login, pr).queryKey,
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
          scope === PULL_REQUEST_STATUS &&
          repo === pr.repo &&
          number === pr.number,
      },
      (old) => (old ? { ...old, draft } : old)
    )
  setDraft(false)
  return () => setDraft(true)
}
