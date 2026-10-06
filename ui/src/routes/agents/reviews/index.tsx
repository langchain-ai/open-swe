import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  QueueList,
  QueueRow,
} from "@langchain/gtm-platform-design-system/patterns/queue-row"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  Tabs,
  TabsList,
  TabsTrigger,
} from "@langchain/gtm-platform-design-system/ui/tabs"

import { AlertTriangle, GitPullRequest, RefreshCw } from "@/components/glyphs"
import type { ReviewSummary } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { BROWSER_CACHE_MAX_AGE_MS, expiresInBrowser } from "@/lib/query"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import { MyPullRequests } from "@/features/reviews/MyPullRequests"
import { OpenPullRequestInput } from "@/features/reviews/OpenPullRequestInput"
import { ReviewBookmarklet } from "@/features/reviews/ReviewBookmarklet"
import { PullRequestRowMenu } from "@/features/reviews/PullRequestLinks"
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
  if (review.status === "running")
    return (
      <Badge tier="quiet" tone="attention" dot>
        Reviewing
      </Badge>
    )
  if (review.status === "error")
    return (
      <Badge tier="quiet" tone="risk">
        Failed
      </Badge>
    )
  if (review.counts.bugs > 0)
    return (
      <Badge tier="quiet" tone="risk">
        {review.counts.bugs} {review.counts.bugs === 1 ? "bug" : "bugs"}
      </Badge>
    )
  return (
    <Badge tier="quiet" tone="positive">
      No bugs
    </Badge>
  )
}

function flagsLabel(review: ReviewSummary) {
  return `${review.counts.flags} ${review.counts.flags === 1 ? "flag" : "flags"}`
}

const tabs = [
  ["mine", "Mine"],
  ["all", "All Reviews"],
] as const

function ReviewsPage() {
  const session = useSession()
  const queryClient = useQueryClient()
  const filters = Route.useSearch()
  const navigate = Route.useNavigate()
  const navigateTo = useNavigate()
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
    <Stack render={<main />} className="min-h-0 min-w-0 flex-1 overflow-hidden">
      <Stack className="min-h-0 w-full flex-1 px-6 py-6">
        {/* One width for both tabs: switching tabs must not re-centre the
              page under the button being clicked. */}
        <Stack
          gap="lg"
          className={cn(
            "mx-auto min-h-0 w-full flex-1",
            !selection && "max-w-work"
          )}
        >
          <Inline gap="md" wrap>
            <Box
              render={<h1 />}
              className="text-page font-semibold tracking-tightish text-ink"
            >
              Pull Requests
            </Box>
            <Tabs
              value={mine ? "mine" : "all"}
              onValueChange={(value: unknown) =>
                changeFilters({
                  tab: value === "all" ? "all" : undefined,
                  page: undefined,
                })
              }
            >
              <TabsList>
                {tabs.map(([value, label]) => (
                  <TabsTrigger
                    key={value}
                    value={value}
                    onPointerEnter={() => prefetch(value === "mine", 0)}
                    onFocus={() => prefetch(value === "mine", 0)}
                  >
                    {label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Inline gap="sm" wrap className="ml-auto">
              <OpenPullRequestInput />
              <ReviewBookmarklet />
              <span className="hidden text-meta text-ink-subtle lg:inline">
                Drag to your bookmarks bar
              </span>
              {!mine && (
                <Button
                  size="compact"
                  variant="outline"
                  disabled={reviews.isFetching}
                  onClick={() => void reviews.refetch()}
                >
                  <Icon icon={RefreshCw} size="sm" />
                  Refresh
                </Button>
              )}
            </Inline>
          </Inline>

          {mine ? (
            session.data && (
              <MyPullRequests
                login={session.data.login}
                filters={filters}
                onFiltersChange={changeFilters}
              />
            )
          ) : (
            <Stack
              aria-busy={reviews.isFetching}
              bg="panel"
              border="line"
              radius="panel"
              className="min-h-0 flex-1 overflow-y-auto"
            >
              {reviews.isFetching && reviews.data && (
                <Inline
                  role="status"
                  gap="sm"
                  className="border-b border-line px-4 py-3 text-meta text-ink-subtle"
                >
                  <Spinner size="sm" />
                  Loading page {page + 1}…
                </Inline>
              )}
              {reviews.isLoading && (
                <Stack className="divide-y divide-line">
                  {[0, 1, 2, 3, 4].map((index) => (
                    <Inline key={index} gap="md" className="h-row-record px-4">
                      <Skeleton className="h-3 w-1/3" />
                      <Skeleton className="ml-auto h-3 w-24" />
                    </Inline>
                  ))}
                </Stack>
              )}
              {reviews.error && (
                <Box padding="md">
                  <StateNotice
                    tone="RISK"
                    icon={AlertTriangle}
                    title="Could not load reviews"
                    description={reviews.error.message}
                    action={
                      <Button
                        size="compact"
                        variant="outline"
                        onClick={() => void reviews.refetch()}
                      >
                        Try again
                      </Button>
                    }
                  />
                </Box>
              )}
              {reviews.data && items.length === 0 && (
                <EmptyState
                  icon={GitPullRequest}
                  title="No reviews yet"
                  description="Enable repositories under Open SWE Review settings and open a PR."
                />
              )}
              {items.length > 0 && (
                <Box
                  className={cn(
                    reviews.isPlaceholderData &&
                      "opacity-50 motion-reduce:transition-none"
                  )}
                >
                  <QueueList label="Reviews">
                    {items.map((review) => (
                      <QueueRow
                        key={review.thread_id}
                        density="record"
                        primary={review.title}
                        secondary={`${review.owner}/${review.repo}#${review.number}`}
                        summary={
                          review.author ? `by ${review.author}` : undefined
                        }
                        state={statusBadge(review)}
                        meta={flagsLabel(review)}
                        onSelect={() =>
                          void navigateTo({
                            to: "/agents/reviews/$owner/$repo/$number",
                            params: {
                              owner: review.owner,
                              repo: review.repo,
                              number: String(review.number),
                            },
                          })
                        }
                        action={
                          <PullRequestRowMenu
                            repo={`${review.owner}/${review.repo}`}
                            number={review.number}
                            title={review.title}
                          />
                        }
                      />
                    ))}
                  </QueueList>
                </Box>
              )}
              {(page > 0 || reviews.data?.has_more) && (
                <Inline
                  gap="lg"
                  justify="between"
                  className="mt-auto border-t border-line px-4 py-2 text-label"
                >
                  <span className="text-ink-subtle tabular-nums">
                    Page {page + 1}
                  </span>
                  <Inline gap="sm">
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
                  </Inline>
                </Inline>
              )}
            </Stack>
          )}
        </Stack>
      </Stack>
    </Stack>
  )
}
