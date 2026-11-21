import type { ReviewPageRef } from "@/features/agents/lib/types"

export function reviewPageRoute(review: ReviewPageRef) {
  return {
    to: "/agents/reviews/$owner/$repo/$number",
    params: {
      owner: review.owner,
      repo: review.repo,
      number: String(review.number),
    },
  } as const
}
