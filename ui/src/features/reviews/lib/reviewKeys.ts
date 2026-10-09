import { queryOptions, useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { WebRequestError } from "@/lib/web-fetch"
import { BROWSER_CACHE_MAX_AGE_MS } from "@/lib/query"
import type { ReviewPageRef } from "@/features/agents/lib/types"
import { reviewChatQuery } from "@/features/agents/lib/queries"
import { pullRequestTopic } from "@/features/reviews/lib/cache"

export type PullRequestRef = ReviewPageRef

/** Query keys for one PR's reads; kept apart from the page so routes can watch them without loading it. */
export const reviewKeys = {
  detail: ({ owner, repo, number }: PullRequestRef) =>
    ["review", owner, repo, number] as const,
  diff: ({ owner, repo, number }: PullRequestRef) =>
    ["reviewDiff", owner, repo, number] as const,
  conversation: ({ owner, repo, number }: PullRequestRef) =>
    ["review-conversation", owner, repo, number] as const,
}

/** The PR as the status cache and its topic name it: `repo` is `owner/name`. */
export function statusRef({ owner, repo, number }: PullRequestRef) {
  return { repo: `${owner}/${repo}`, number }
}

// Every file header and note observes these, so each one scrolling in would
// otherwise revalidate them; a missing PR stays missing, so 4xx isn't retried.
// The backend says when the PR or its review changes, so nothing polls.
export function sharedReviewOptions(pr: PullRequestRef) {
  return {
    gcTime: BROWSER_CACHE_MAX_AGE_MS,
    staleTime: 60_000,
    retry: (failures: number, error: Error) => {
      const status = error instanceof WebRequestError ? error.status : 0
      return failures < 1 && !(status >= 400 && status < 500)
    },
    meta: { invalidatedBy: [pullRequestTopic(statusRef(pr))] },
  } as const
}

// Every observer writes its options onto the shared query, so each must pass these whole.
export const reviewDetailQuery = (pr: PullRequestRef) =>
  queryOptions({
    queryKey: reviewKeys.detail(pr),
    queryFn: () => api.getReview(pr.owner, pr.repo, pr.number),
    ...sharedReviewOptions(pr),
  })

/** The PR's chat. Reading it creates the thread, so it waits until the page has found the PR. */
export function useReviewChat(pr: PullRequestRef) {
  const found = useQuery(reviewDetailQuery(pr)).isSuccess
  return useQuery({ ...reviewChatQuery(pr), enabled: found })
}
