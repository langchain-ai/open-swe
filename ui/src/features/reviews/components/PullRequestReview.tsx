import type { ReviewSummary } from "@/lib/api"
import { ReviewIndicators } from "./ReviewIndicators"

export function PullRequestReview({
  summary,
  pending,
  known,
}: {
  summary: ReviewSummary | null | undefined
  pending: boolean
  known: boolean
}) {
  if (summary) return <ReviewIndicators review={summary} />
  if (pending) return <span>Loading review…</span>
  if (!known) return <span>Review unavailable</span>
  return <span>Not reviewed</span>
}
