import { ArrowUpRight, ClipboardCheck } from "lucide-react"

import { useThreadSelfReview } from "@/features/agents/lib/queries"
import { selfReviewSummary } from "@/features/agents/components/SelfReviewPanel"

/**
 * Transcript pointer to the Review surface. The findings live in the panel;
 * this only says they exist, because the agent's own message already explains
 * what it did about them.
 *
 * Rendered only for a thread that opened a PR — a self-review cannot exist
 * without one, and mounting this on every thread would poll for nothing.
 */
export function SelfReviewCard({
  threadId,
  onOpen,
  pollWhileActive = false,
}: {
  threadId: string
  onOpen?: () => void
  pollWhileActive?: boolean
}) {
  const query = useThreadSelfReview(threadId, { pollWhileActive })
  const review = query.data?.reviews[0]
  if (!review) return null

  const deferred = review.findings.filter(
    (finding) => finding.disposition === "deferred"
  ).length

  return (
    <button
      type="button"
      data-testid="self-review-card"
      aria-label="Open self-review findings"
      onClick={() => onOpen?.()}
      className="group mt-4 flex w-full items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left shadow-sm transition-[border-color,box-shadow] hover:border-foreground/25 hover:shadow-md"
    >
      <ClipboardCheck className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">
          Self-review of PR #{review.prNumber}
        </span>
        <span className="block truncate text-xs text-muted-foreground">
          {selfReviewSummary(review)}
          {deferred > 0 ? " — waiting on you" : ""}
        </span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-foreground px-2.5 py-1.5 text-xs font-medium text-background">
        Open review
        <ArrowUpRight className="size-3.5" />
      </span>
    </button>
  )
}
