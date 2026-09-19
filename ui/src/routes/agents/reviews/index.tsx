import { createFileRoute } from "@tanstack/react-router"
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { GitPullRequestIcon } from "@phosphor-icons/react"

import type { ReviewSummary } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { MyPullRequests } from "@/features/reviews/MyPullRequests"
import { PullRequestLinks } from "@/features/reviews/PullRequestLinks"
import { ReviewCounts } from "@/features/reviews/components/ReviewCounts"
import {
  validateReviewsSearch,
  type ReviewsSearch,
} from "@/features/reviews/search"

export const Route = createFileRoute("/agents/reviews/")({
  validateSearch: validateReviewsSearch,
  component: ReviewsPage,
})

function statusBadge(review: ReviewSummary) {
  if (review.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
        Reviewing
      </span>
    )
  }
  if (review.status === "error") {
    return <span className="text-xs text-destructive">Failed</span>
  }
  return null
}

function ReviewsPage() {
  const session = useSession()
  const queryClient = useQueryClient()
  const filters = Route.useSearch()
  const navigate = Route.useNavigate()
  const mine = filters.tab !== "all"
  const page = filters.page ?? 0
  const changeFilters = (changes: Partial<ReviewsSearch>, replace = false) => {
    void navigate({
      search: (previous) => ({
        ...previous,
        ...changes,
        ...(Object.keys(changes).some((key) =>
          ["repo", "q", "status", "sort", "direction"].includes(key)
        )
          ? { page: undefined }
          : {}),
      }),
      replace,
    })
  }
  const reviews = useQuery({
    queryKey: ["reviews", mine, page],
    queryFn: () => api.listReviews(page, mine),
    enabled: !!session.data && !mine,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const prefetch = (nextMine: boolean, nextPage: number) => {
    if (nextPage < 0 || nextMine) return
    void queryClient.prefetchQuery({
      queryKey: ["reviews", nextMine, nextPage],
      queryFn: () => api.listReviews(nextPage, nextMine),
      staleTime: Infinity,
    })
  }

  const items = reviews.data?.reviews ?? []

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {/* One width for both tabs: switching tabs must not re-centre the page
          under the button being clicked. */}
      <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col px-6 py-8">
        <h1 className="font-heading text-base font-medium text-foreground">
          Pull Requests
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          {mine
            ? "Your open pull requests, live CI checks, and merge status."
            : "Pull requests reviewed by Open SWE Review. Click into one for the full analysis."}
        </p>

        <div className="mt-6 flex items-center gap-1">
          {!mine && (
            <Button
              className="order-last ml-auto"
              size="sm"
              variant="outline"
              disabled={reviews.isFetching}
              onClick={() => void reviews.refetch()}
            >
              Refresh
            </Button>
          )}
          {(
            [
              [true, "Mine"],
              [false, "All Reviews"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                changeFilters({
                  tab: value ? undefined : "all",
                  page: undefined,
                })
              }}
              onPointerEnter={() => prefetch(value, 0)}
              onFocus={() => prefetch(value, 0)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                mine === value
                  ? "bg-sidebar-row-hover font-medium text-foreground"
                  : "text-muted-foreground hover:bg-sidebar-row-hover"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {mine ? (
          session.data && (
            <MyPullRequests
              login={session.data.login}
              filters={filters}
              onFiltersChange={changeFilters}
            />
          )
        ) : (
          <div
            aria-busy={reviews.isFetching}
            className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-lg border border-border bg-card"
          >
            {reviews.isFetching && reviews.data && (
              <p
                role="status"
                className="border-b border-border px-4 py-3 text-xs text-muted-foreground"
              >
                Loading page {page + 1}…
              </p>
            )}
            {reviews.isLoading && (
              <div className="p-4">
                <Skeleton className="h-24 w-full" />
              </div>
            )}
            {reviews.error && (
              <p className="px-4 py-3 text-xs text-destructive">
                {reviews.error.message}
              </p>
            )}
            {reviews.data && items.length === 0 && (
              <p className="px-4 py-3 text-xs text-muted-foreground">
                {mine
                  ? "No reviews on your PRs yet. Switch to All to see every review you have access to."
                  : "No reviews yet. Enable repositories under Open SWE Review settings and open a PR."}
              </p>
            )}
            <div
              className={cn(
                "divide-y divide-border",
                reviews.isPlaceholderData && "opacity-50"
              )}
            >
              {items.map((review) => (
                <div
                  key={review.thread_id}
                  className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-sidebar-row-hover"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <GitPullRequestIcon className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-foreground">
                        {review.title}
                      </div>
                      <PullRequestLinks
                        repo={`${review.owner}/${review.repo}`}
                        number={review.number}
                        title={review.title}
                      />
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {review.owner}/{review.repo}#{review.number}
                        {review.author && !mine && (
                          <span className="ml-2">by {review.author}</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-xs">
                    {statusBadge(review)}
                    <ReviewCounts counts={review.counts} />
                  </div>
                </div>
              ))}
            </div>
            {(page > 0 || reviews.data?.has_more) && (
              <div className="flex items-center justify-between gap-4 border-t border-border px-4 py-2 text-xs">
                <span className="text-muted-foreground">Page {page + 1}</span>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={page === 0 || reviews.isFetching}
                    onPointerEnter={() => prefetch(mine, page - 1)}
                    onClick={() =>
                      changeFilters({
                        page: Math.max(0, page - 1) || undefined,
                      })
                    }
                  >
                    Prev
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!reviews.data?.has_more || reviews.isFetching}
                    onPointerEnter={() => prefetch(mine, page + 1)}
                    onClick={() => changeFilters({ page: page + 1 })}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  )
}
