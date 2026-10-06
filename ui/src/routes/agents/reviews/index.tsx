import { createFileRoute } from "@tanstack/react-router"
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { GitPullRequestIcon } from "@phosphor-icons/react"

import type { ReviewSummary } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { BROWSER_CACHE_MAX_AGE_MS, expiresInBrowser } from "@/lib/query"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { MyPullRequests } from "@/features/reviews/MyPullRequests"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { ReviewBookmarklet } from "@/features/reviews/ReviewBookmarklet"
import { PullRequestLinks } from "@/features/reviews/PullRequestLinks"
import { ReviewCounts } from "@/features/reviews/components/ReviewCounts"
import {
  parsePullRequestSelection,
  validateReviewsSearch,
  type ReviewsSearch,
} from "@/features/reviews/search"

export const Route = createFileRoute("/agents/reviews/")({
  validateSearch: validateReviewsSearch,
  head: () => ({ meta: [{ title: pageTitle("Pull Requests") }] }),
  component: ReviewsPage,
})

function statusBadge(review: ReviewSummary) {
  if (review.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-meta text-ink-subtle">
        <span className="size-1.5 animate-pulse rounded-full bg-attention" />
        Reviewing
      </span>
    )
  }
  if (review.status === "error") {
    return <span className="text-label text-risk">Failed</span>
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
        ...("pr" in changes ? { files: undefined, at: undefined } : {}),
      }),
      replace,
    })
  }
  const reviews = useQuery({
    queryKey: ["reviews", mine, page],
    queryFn: () => api.listReviews(page, mine),
    enabled: !!session.data && !mine,
    placeholderData: keepPreviousData,
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const prefetch = (nextMine: boolean, nextPage: number) => {
    if (nextPage < 0 || nextMine) return
    void queryClient.prefetchQuery({
      queryKey: ["reviews", nextMine, nextPage],
      queryFn: () => api.listReviews(nextPage, nextMine),
      staleTime: BROWSER_CACHE_MAX_AGE_MS,
    })
  }

  const items = reviews.data?.reviews ?? []

  const selection = mine ? parsePullRequestSelection(filters.pr) : null

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-0 w-full flex-1 flex-col px-6 py-6">
        {/* One width for both tabs: switching tabs must not re-centre the
              page under the button being clicked. */}
        <div
          className={cn(
            "mx-auto flex min-h-0 w-full flex-1 flex-col",
            !selection && "max-w-6xl"
          )}
        >
          <div className="flex items-center gap-3">
            <h1 className="text-title font-medium text-ink">
              Pull Requests
            </h1>
            <div className="flex items-center gap-1">
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
                    "rounded-badge px-2.5 py-1 text-label transition-colors",
                    mine === value
                      ? "bg-hover font-medium text-ink"
                      : "text-ink-subtle hover:bg-hover"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <OpenPullRequestInput />
            <ReviewBookmarklet />
            <span className="hidden text-meta text-ink-subtle lg:inline">
              Drag to your bookmarks bar
            </span>
            {!mine && (
              <Button
                className="ml-auto"
                size="compact"
                variant="outline"
                disabled={reviews.isFetching}
                onClick={() => void reviews.refetch()}
              >
                Refresh
              </Button>
            )}
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
              className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-compact border border-line bg-panel"
            >
              {reviews.isFetching && reviews.data && (
                <p
                  role="status"
                  className="border-b border-line px-4 py-3 text-meta text-ink-subtle"
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
                <p className="px-4 py-3 text-label text-risk">
                  {reviews.error.message}
                </p>
              )}
              {reviews.data && items.length === 0 && (
                <p className="px-4 py-3 text-meta text-ink-subtle">
                  {mine
                    ? "No reviews on your PRs yet. Switch to All to see every review you have access to."
                    : "No reviews yet. Enable repositories under Open SWE Review settings and open a PR."}
                </p>
              )}
              <div
                className={cn(
                  "divide-y divide-line",
                  reviews.isPlaceholderData && "opacity-50"
                )}
              >
                {items.map((review) => (
                  <div
                    key={review.thread_id}
                    className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-hover"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <GitPullRequestIcon className="size-4 shrink-0 text-ink-subtle" />
                      <div className="min-w-0">
                        <div className="truncate text-label font-medium text-ink">
                          {review.title}
                        </div>
                        <PullRequestLinks
                          repo={`${review.owner}/${review.repo}`}
                          number={review.number}
                          title={review.title}
                        />
                        <div className="mt-0.5 text-meta text-ink-subtle">
                          {review.owner}/{review.repo}#{review.number}
                          {review.author && !mine && (
                            <span className="ml-2">by {review.author}</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 text-label">
                      {statusBadge(review)}
                      <ReviewCounts counts={review.counts} />
                    </div>
                  </div>
                ))}
              </div>
              {(page > 0 || reviews.data?.has_more) && (
                <div className="flex items-center justify-between gap-4 border-t border-line px-4 py-2 text-label">
                  <span className="text-ink-subtle">Page {page + 1}</span>
                  <div className="flex items-center gap-2">
                    <Button
                      size="compact"
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
                      size="compact"
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
      </div>
    </main>
  )
}
