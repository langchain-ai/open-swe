import type { ReviewSummary } from "@/lib/api"
import { ReviewCounts } from "./ReviewCounts"

export function ReviewIndicators({ review }: { review: ReviewSummary }) {
  return (
    <a
      href={`/agents/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`}
      className="inline-flex flex-col gap-1.5 hover:underline"
      aria-label={`Open review: ${review.counts.bugs} bugs, ${review.counts.flags} flags`}
    >
      <span className="flex gap-3">
        <ReviewCounts counts={review.counts} withLabels />
      </span>
      {review.status === "running" && (
        <span className="text-amber-700 dark:text-amber-400">Reviewing…</span>
      )}
      {review.status === "error" && (
        <span className="text-destructive">Review failed</span>
      )}
    </a>
  )
}
