import { Link, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  BugBeetleIcon,
  FlagIcon,
  GitPullRequestIcon,
} from "@phosphor-icons/react"

import type { ReviewSummary } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/agents/reviews/")({
  component: ReviewsPage,
})

function statusBadge(review: ReviewSummary) {
  if (review.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--ui-text-muted)]">
        <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
        Reviewing
      </span>
    )
  }
  if (review.status === "error") {
    return <span className="text-xs text-[var(--ui-danger)]">Failed</span>
  }
  return null
}

function ReviewsPage() {
  const session = useSession()
  const reviews = useQuery({
    queryKey: ["reviews"],
    queryFn: api.listReviews,
    enabled: !!session.data,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "running") ? 5000 : false,
  })

  return (
    <main className="min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <h1 className="font-heading text-lg font-medium text-[var(--ui-text)]">
          PR Reviews
        </h1>
        <p className="mt-1 text-xs text-[var(--ui-text-muted)]">
          Pull requests reviewed by Open SWE Review. Click into one for the full
          analysis.
        </p>

        <div className="mt-6 overflow-hidden rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)]">
          {reviews.isLoading && (
            <div className="p-4">
              <Skeleton className="h-24 w-full" />
            </div>
          )}
          {reviews.error && (
            <p className="px-4 py-3 text-xs text-[var(--ui-danger)]">
              {reviews.error.message}
            </p>
          )}
          {reviews.data && reviews.data.length === 0 && (
            <p className="px-4 py-3 text-xs text-[var(--ui-text-muted)]">
              No reviews yet. Enable repositories under Open SWE Review settings
              and open a PR.
            </p>
          )}
          <div className="divide-y divide-[var(--ui-border)]">
            {(reviews.data ?? []).map((review) => (
              <Link
                key={review.thread_id}
                to="/agents/reviews/$owner/$repo/$number"
                params={{
                  owner: review.owner,
                  repo: review.repo,
                  number: String(review.number),
                }}
                className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-[var(--ui-sidebar-hover)]"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <GitPullRequestIcon className="size-4 shrink-0 text-[var(--ui-text-muted)]" />
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-[var(--ui-text)]">
                      {review.title}
                    </div>
                    <div className="mt-0.5 text-xs text-[var(--ui-text-muted)]">
                      {review.owner}/{review.repo}#{review.number}
                      {review.head_ref && (
                        <span className="ml-2 font-mono text-[11px]">
                          {review.head_ref}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3 text-xs">
                  {statusBadge(review)}
                  <span
                    className={cn(
                      "inline-flex items-center gap-1",
                      review.counts.bugs > 0
                        ? "text-[var(--ui-danger)]"
                        : "text-[var(--ui-text-muted)]"
                    )}
                  >
                    <BugBeetleIcon className="size-3.5" />
                    {review.counts.bugs}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[var(--ui-text-muted)]">
                    <FlagIcon className="size-3.5" />
                    {review.counts.flags}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </main>
  )
}
