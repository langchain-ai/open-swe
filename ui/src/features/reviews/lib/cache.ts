import type { InfiniteData, QueryClient } from "@tanstack/react-query"

import {
  api,
  type OpenPullRequest,
  type OpenPullRequestsPayload,
} from "@/lib/api"
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

/** Invalidated as the pull request's checks run, at most every 15s; `repo` is `owner/name`. */
export function pullRequestChecksTopic(pr: PullRequestRef) {
  return invalidationTopic(
    "pull-request-checks",
    `${pr.repo}/${pr.number}`.toLowerCase()
  )
}

/** A pull request's live state on GitHub, shared by every page that shows it. */
export function pullRequestStatusQuery(login: string, pr: PullRequestRef) {
  return {
    queryKey: [PULL_REQUEST_STATUS, login, pr.repo, pr.number],
    queryFn: () => api.pullRequestStatus(pr.repo, pr.number),
    meta: {
      invalidatedBy: [
        pullRequestTopic(pr),
        pullRequestChecksTopic(pr),
        invalidationTopic("required-checks", pr.repo.toLowerCase()),
      ],
    },
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

export class PullRequestCache {
  constructor(
    private readonly queryClient: QueryClient,
    private readonly pr: PullRequestRef
  ) {}

  setAutoMerge(autoMerge: boolean): void {
    const repo = this.pr.repo.toLowerCase()
    const status = {
      predicate: ({
        queryKey: [scope, , name, number],
      }: {
        queryKey: readonly unknown[]
      }) =>
        scope === PULL_REQUEST_STATUS &&
        typeof name === "string" &&
        name.toLowerCase() === repo &&
        number === this.pr.number,
    }
    void this.queryClient.cancelQueries(status)
    for (const query of this.queryClient.getQueryCache().findAll(status)) {
      const old = query.state.data as OpenPullRequest | null | undefined
      if (old && old.autoMerge !== autoMerge) {
        this.queryClient.setQueryData(
          query.queryKey,
          { ...old, autoMerge },
          {
            updatedAt: query.state.dataUpdatedAt,
          }
        )
      }
    }
    for (const query of this.queryClient
      .getQueryCache()
      .findAll({ queryKey: ["open-pull-requests"] })) {
      const current = query.state.data as
        | InfiniteData<OpenPullRequestsPayload>
        | undefined
      if (
        !current?.pages.some((page) =>
          page.pullRequests.some(
            (row) =>
              row.repo.toLowerCase() === repo && row.number === this.pr.number
          )
        )
      )
        continue
      void this.queryClient.cancelQueries({
        queryKey: query.queryKey,
        exact: true,
      })
      const old = query.state.data as
        | InfiniteData<OpenPullRequestsPayload>
        | undefined
      if (!old) continue
      this.queryClient.setQueryData(
        query.queryKey,
        {
          ...old,
          pages: old.pages.map((page) => ({
            ...page,
            pullRequests: page.pullRequests.map((row) =>
              row.repo.toLowerCase() === repo && row.number === this.pr.number
                ? { ...row, autoMerge }
                : row
            ),
          })),
        },
        { updatedAt: query.state.dataUpdatedAt }
      )
    }
  }
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
