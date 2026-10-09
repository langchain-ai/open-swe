import { skipToken, useQuery } from "@tanstack/react-query"

import type { ReviewPageRef } from "@/features/agents/lib/types"
import { reviewChatQuery } from "@/features/agents/lib/queries"

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

/** The PR's chat. Reading it creates the thread, so it waits until the page has found the PR. */
export function useReviewChat(pr: PullRequestRef, enabled = true) {
  const found = useQuery({
    queryKey: reviewKeys.detail(pr),
    queryFn: skipToken,
  }).isSuccess
  return useQuery({ ...reviewChatQuery(pr), enabled: enabled && found })
}
