import type { ReviewSummary } from "@/lib/api"
import { ReviewCounts } from "./ReviewCounts"

export function ReviewIndicators({ review }: { review: ReviewSummary }) {
  return (
    <a
      href={`/agents/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`}
      className="inline-flex flex-col gap-space-2 hover:underline"
      aria-label={`Open review: ${review.counts.bugs} bugs, ${review.counts.flags} flags`}
    >
      <span className="flex gap-space-3">
        <ReviewCounts counts={review.counts} withLabels />
      </span>
      {review.status === "running" && (
        <span className="text-status-yellow">Reviewing…</span>
      )}
      {review.status === "error" && (
        <span className="text-error-secondary">Review failed</span>
      )}
    </a>
  )
}
