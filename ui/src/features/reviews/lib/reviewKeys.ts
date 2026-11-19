import { queryOptions, useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { BROWSER_CACHE_MAX_AGE_MS } from "@/lib/query"
import type { ReviewPageRef } from "@/features/agents/lib/types"
import {
  pullRequestChecksTopic,
  pullRequestTopic,
} from "@/features/reviews/lib/cache"

export type PullRequestRef = ReviewPageRef

/** Query keys for one PR's reads; kept apart from the page so routes can watch them without loading it. */
export const reviewKeys = {
  detail: ({ owner, repo, number }: PullRequestRef) =>
    ["review", owner, repo, number] as const,
  diff: ({ owner, repo, number }: PullRequestRef) =>
    ["reviewDiff", owner, repo, number] as const,
  checks: ({ owner, repo, number }: PullRequestRef) =>
    ["review-checks", owner, repo, number] as const,
  conversation: ({ owner, repo, number }: PullRequestRef) =>
    ["review-conversation", owner, repo, number] as const,
}

/** The PR as the status cache and its topic name it: `repo` is `owner/name`. */
export function statusRef({ owner, repo, number }: PullRequestRef) {
  return { repo: `${owner}/${repo}`, number }
}

const reviewReadOptions = {
  gcTime: BROWSER_CACHE_MAX_AGE_MS,
  staleTime: 60_000,
  retry: (failures: number, error: Error) => {
    const status = error instanceof DashboardRequestError ? error.status : 0
    return failures < 1 && (status === 429 || !(status >= 400 && status < 500))
  },
  retryDelay: (_attempt: number, error: Error) =>
    error instanceof DashboardRequestError && error.status === 429
      ? (error.retryAfterMs ?? 30_000)
      : 1000,
} as const

// The backend says when the PR or its review changes, so nothing polls.
export function sharedReviewOptions(pr: PullRequestRef) {
  return {
    ...reviewReadOptions,
    meta: { invalidatedBy: [pullRequestTopic(statusRef(pr))] },
  } as const
}

// CI changes check runs far more often than anything else on the PR, so they
// have a topic of their own that the detail and conversation don't hear.
export function reviewChecksOptions(pr: PullRequestRef) {
  return {
    ...reviewReadOptions,
    meta: {
      invalidatedBy: [
        pullRequestTopic(statusRef(pr)),
        pullRequestChecksTopic(statusRef(pr)),
      ],
    },
  } as const
}

// The diff changes only with the PR's head or base, which the review page
// watches, so check runs and review activity on the PR never reload it.
export const reviewDiffOptions = {
  ...reviewReadOptions,
  staleTime: Infinity,
} as const

// Every observer writes its options onto the shared query, so each must pass these whole.
export const reviewDetailQuery = (pr: PullRequestRef) =>
  queryOptions({
    queryKey: reviewKeys.detail(pr),
    queryFn: () => api.getReview(pr.owner, pr.repo, pr.number),
    ...sharedReviewOptions(pr),
  })

/**
 * The PR's chat: the person's thread for it, which reading it finds or makes.
 * Read alongside the PR, since the backend confirms the PR exists before it makes one.
 */
export function reviewChatQuery(pr: PullRequestRef) {
  return {
    queryKey: ["review-chat", pr.owner, pr.repo, pr.number],
    queryFn: () => api.getReviewChat(pr.owner, pr.repo, pr.number),
    retry: reviewReadOptions.retry,
    retryDelay: reviewReadOptions.retryDelay,
  } as const
}

export function useReviewChat(pr: PullRequestRef) {
  return useQuery(reviewChatQuery(pr))
}
