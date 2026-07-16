import { Link, createFileRoute } from "@tanstack/react-router"
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import {
  BugBeetleIcon,
  FlagIcon,
  GitPullRequestIcon,
} from "@phosphor-icons/react"

import type { ReviewSummary } from "@/lib/api"
import { Button } from "@/components/ui/button"
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
  const queryClient = useQueryClient()
  const [mine, setMine] = useState(true)
  const [page, setPage] = useState(0)
  const reviews = useQuery({
    queryKey: ["reviews", mine, page],
    queryFn: () => api.listReviews(page, mine),
    enabled: !!session.data,
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.reviews.some((r) => r.status === "running")
        ? 5000
        : false,
  })

  const prefetch = (nextMine: boolean, nextPage: number) => {
    if (nextPage < 0) return
    void queryClient.prefetchQuery({
      queryKey: ["reviews", nextMine, nextPage],
      queryFn: () => api.listReviews(nextPage, nextMine),
    })
  }

  const items = reviews.data?.reviews ?? []

  return (
    <main className="min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <h1 className="font-heading text-base font-medium text-[var(--ui-text)]">
          PR Reviews
        </h1>
        <p className="mt-1 text-xs text-[var(--ui-text-muted)]">
          Pull requests reviewed by Jarvis Review. Click into one for the full
          analysis.
        </p>

        <div className="mt-6 flex items-center gap-1">
          {(
            [
              [true, "My PRs"],
              [false, "All"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                setMine(value)
                setPage(0)
              }}
              onPointerEnter={() => prefetch(value, 0)}
              onFocus={() => prefetch(value, 0)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                mine === value
                  ? "bg-[var(--ui-sidebar-hover)] font-medium text-[var(--ui-text)]"
                  : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="mt-3 overflow-hidden rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)]">
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
          {reviews.data && items.length === 0 && (
            <p className="px-4 py-3 text-xs text-[var(--ui-text-muted)]">
              {mine
                ? "No reviews on your PRs yet. Switch to All to see every review you have access to."
                : "No reviews yet. Enable repositories under Jarvis Review settings and open a PR."}
            </p>
          )}
          <div className="divide-y divide-[var(--ui-border)]">
            {items.map((review) => (
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
                      {review.author && !mine && (
                        <span className="ml-2">by {review.author}</span>
                      )}
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
          {(page > 0 || reviews.data?.has_more) && (
            <div className="flex items-center justify-between gap-4 border-t border-[var(--ui-border)] px-4 py-2 text-xs">
              <span className="text-[var(--ui-text-muted)]">
                Page {page + 1}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page === 0}
                  onPointerEnter={() => prefetch(mine, page - 1)}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Prev
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!reviews.data?.has_more}
                  onPointerEnter={() => prefetch(mine, page + 1)}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
