import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"

import type { ReviewSummary } from "@/lib/api"
import { ReviewCounts } from "./ReviewCounts"

export function ReviewIndicators({ review }: { review: ReviewSummary }) {
  return (
    <Stack
      render={
        <a
          href={`/agents/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`}
          aria-label={`Open review: ${review.counts.bugs} bugs, ${review.counts.flags} flags`}
        />
      }
      gap="xs"
      className="hover:underline"
    >
      <Inline gap="md">
        <ReviewCounts counts={review.counts} withLabels />
      </Inline>
      {review.status === "running" && (
        <span className="text-attention">Reviewing…</span>
      )}
      {review.status === "error" && (
        <span className="text-risk">Review failed</span>
      )}
    </Stack>
  )
}
