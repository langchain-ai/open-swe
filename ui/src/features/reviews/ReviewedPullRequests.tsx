import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { api, type ReviewSummary } from "@/lib/api"
import { BROWSER_CACHE_MAX_AGE_MS, expiresInBrowser } from "@/lib/query"
import { cn } from "@/lib/utils"
import { PullRequestLinks } from "./PullRequestLinks"
import { ReviewCounts } from "./components/ReviewCounts"

function statusBadge(review: ReviewSummary) {
  if (review.status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-secondary">
        <span className="size-1.5 animate-pulse rounded-full bg-warning-strong" />
        Reviewing
      </span>
    )
  }
  if (review.status === "error") {
    return <span className="text-xs text-error-secondary">Failed</span>
  }
  return null
}

export function ReviewedPullRequests({
  page,
  onPageChange,
}: {
  page: number
  onPageChange: (page: number) => void
}) {
  const queryClient = useQueryClient()
  const reviews = useQuery({
    queryKey: ["reviews", false, page],
    queryFn: () => api.listReviews(page, false),
    placeholderData: keepPreviousData,
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const prefetch = (nextPage: number) => {
    if (nextPage < 0) return
    void queryClient.prefetchQuery({
      queryKey: ["reviews", false, nextPage],
      queryFn: () => api.listReviews(nextPage, false),
      staleTime: BROWSER_CACHE_MAX_AGE_MS,
    })
  }
  const items = reviews.data?.reviews ?? []
  return (
    <>
      <div className="mt-4 flex justify-end">
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          disabled={reviews.isFetching}
          onClick={() => void reviews.refetch()}
        >
          Refresh
        </Button>
      </div>
      <div
        aria-busy={reviews.isFetching}
        className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-lg border border-default bg-surface-level-1"
      >
        {reviews.isFetching && reviews.data && (
          <p
            role="status"
            className="border-b border-default px-4 py-3 text-xs text-secondary"
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
          <p className="px-4 py-3 text-xs text-error-secondary">
            {reviews.error.message}
          </p>
        )}
        {reviews.data && items.length === 0 && (
          <p className="px-4 py-3 text-xs text-secondary">
            No reviews yet. Enable repositories under Open SWE Review settings
            and open a PR.
          </p>
        )}
        <div
          className={cn(
            "divide-y divide-default",
            reviews.isPlaceholderData && "opacity-50"
          )}
        >
          {items.map((review) => (
            <div
              key={review.thread_id}
              className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-surface-level-2-hover"
            >
              <div className="flex min-w-0 items-center gap-3">
                <GitPullRequestIcon
                  weight="regular"
                  className="size-4 shrink-0 text-icon-secondary"
                />
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-primary">
                    {review.title}
                  </div>
                  <PullRequestLinks
                    repo={`${review.owner}/${review.repo}`}
                    number={review.number}
                    title={review.title}
                  />
                  <div className="mt-0.5 text-xs text-secondary">
                    {review.owner}/{review.repo}#{review.number}
                    {review.author && (
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
          <div className="flex items-center justify-between gap-4 border-t border-default px-4 py-2 text-xs">
            <span className="text-secondary">Page {page + 1}</span>
            <div className="flex items-center gap-2">
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                disabled={page === 0 || reviews.isFetching}
                onPointerEnter={() => prefetch(page - 1)}
                onClick={() => onPageChange(Math.max(0, page - 1))}
              >
                Prev
              </Button>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                disabled={!reviews.data?.has_more || reviews.isFetching}
                onPointerEnter={() => prefetch(page + 1)}
                onClick={() => onPageChange(page + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
