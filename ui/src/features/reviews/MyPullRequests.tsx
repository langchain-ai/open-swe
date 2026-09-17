import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { Button } from "@/components/ui/button"
import { MultiSelect } from "@/components/ui/multi-select"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"
import { PullRequestCard } from "./components/PullRequestCard"
import { PullRequestReview } from "./components/PullRequestReview"
import { forgetPullRequest, refreshPullRequest } from "./lib/cache"
import { dateLabel } from "./lib/dateLabel"
import { pullRequestKey, statusLabels } from "./lib/status"
import { control } from "./lib/styles"
import { useOpenPullRequests } from "./lib/useOpenPullRequests"
import { usePullRequestDetails } from "./lib/usePullRequestDetails"
import { reviewStatuses, type ReviewsSearch, type ReviewSort } from "./search"

const pageSize = 10

const sortOptions = [
  ["Last updated", "updatedAt"],
  ["Created", "createdAt"],
] as const

export function MyPullRequests({
  login,
  filters,
  onFiltersChange,
}: {
  login: string
  filters: ReviewsSearch
  onFiltersChange: (changes: Partial<ReviewsSearch>, replace?: boolean) => void
}) {
  const {
    repo = [],
    q: search = "",
    status: filter,
    sort = "updatedAt",
    page = 0,
    direction = "desc",
  } = filters
  const toggleSort = (next: ReviewSort) =>
    onFiltersChange({
      page: undefined,
      sort: next,
      direction: sort === next && direction === "asc" ? "desc" : "asc",
    })
  const queryClient = useQueryClient()
  const query = useOpenPullRequests(login, repo, sort, direction)
  const knownRepos = useRepos()
  const pages = query.data?.pages ?? []
  const latest = pages.at(-1)
  const rows = pages.flatMap((loaded) => loaded.pullRequests)
  const { fetchNextPage, isFetching } = query
  const needsMorePages =
    query.hasNextPage &&
    (Boolean(filter?.length) || (page + 1) * pageSize >= rows.length)
  useEffect(() => {
    if (needsMorePages && !isFetching) void fetchNextPage()
  }, [needsMorePages, isFetching, fetchNextPage])
  const matchingRows = rows
    .filter(
      (pr) =>
        queryClient.getQueryData([
          "my-pr-details",
          login,
          pr.repo,
          pr.number,
        ]) !== null
    )
    .filter((pr) =>
      `${pr.repo} #${pr.number} ${pr.title}`
        .toLowerCase()
        .includes(search.toLowerCase())
    )
  const requestedRows = filter?.length
    ? matchingRows
    : matchingRows.slice(page * pageSize, (page + 1) * pageSize)
  const { all, detailsLoading } = usePullRequestDetails(
    login,
    matchingRows,
    requestedRows
  )
  const filtered = all.filter(
    (pr) =>
      !filter?.length ||
      filter.some((status) => statusLabels(pr).includes(status))
  )
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize)
  const refreshing = query.isFetching && !query.isFetchingNextPage
  const incomplete = pages.some((loaded) => loaded.incomplete)
  const reviewRefs = visible.map((pr) => ({ repo: pr.repo, number: pr.number }))
  const reviews = useQuery({
    queryKey: ["my-pr-review-summaries", login, reviewRefs],
    queryFn: () => api.reviewSummaries(reviewRefs),
    enabled: reviewRefs.length > 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const accessibleRepos = (knownRepos.data?.repositories ?? []).map(
    (known) => known.full_name
  )
  // A timed-out search returns no PRs, and filtering by repository is the way
  // out of it, so the options cannot be derived from the PRs themselves.
  const repoNames = [
    ...new Set([
      ...(accessibleRepos.length ? accessibleRepos : all.map((pr) => pr.repo)),
      ...repo,
    ]),
  ].sort()

  return (
    <section className="mt-5 space-y-4" aria-label="My open pull requests">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span aria-live="polite">
          {refreshing
            ? "Refreshing from GitHub…"
            : latest
              ? `Refreshed ${dateLabel(latest.updatedAt)}`
              : "Live open pull requests from GitHub"}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={query.isFetching}
          onClick={() => {
            queryClient.removeQueries({
              queryKey: ["my-pr-details", login],
              predicate: (cached) => cached.state.data === null,
            })
            void query.refetch()
            void queryClient.invalidateQueries({
              queryKey: ["my-pr-details", login],
            })
            void queryClient.invalidateQueries({
              queryKey: ["pr-thread-status", login],
            })
            if (reviewRefs.length) void reviews.refetch()
          }}
        >
          Refresh
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <MultiSelect
          label="Filter by repository"
          placeholder="All repositories"
          searchPlaceholder="Search repositories…"
          emptyMessage={
            knownRepos.isPending
              ? "Loading repositories…"
              : knownRepos.isError
                ? "Could not load repositories"
                : "No matches"
          }
          options={repoNames}
          value={repo}
          onValueChange={(chosen) =>
            onFiltersChange({ repo: chosen.length ? chosen : undefined })
          }
        />
        <input
          className={cn(control, "min-w-40 flex-1")}
          aria-label="Search pull requests"
          placeholder="Search title or PR number…"
          value={search}
          onChange={(event) =>
            onFiltersChange({ q: event.target.value || undefined }, true)
          }
        />
        <MultiSelect
          label="Filter by status"
          placeholder="All statuses"
          options={reviewStatuses}
          value={filter ?? []}
          onValueChange={(status) =>
            onFiltersChange({ status: status.length ? status : undefined })
          }
        />
      </div>
      {query.error && (
        <p role="alert" className="text-sm text-destructive">
          {latest &&
            (query.isFetchNextPageError
              ? "Could not load more PRs; showing the pages loaded so far. "
              : "Refresh failed; showing the previous snapshot. ")}
          {query.error.message}
        </p>
      )}
      {query.isLoading ? (
        <Skeleton className="h-56 w-full" />
      ) : incomplete ? (
        <p
          role="status"
          className="rounded-lg border border-border bg-card px-4 py-12 text-center text-xs text-amber-700 dark:text-amber-400"
        >
          GitHub&rsquo;s pull request search timed out, and the partial answer
          it returned would have hidden most of your PRs. Filter by repository
          to narrow the search, or refresh to try again.
        </p>
      ) : (
        latest && (
          <>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
              <span className="ml-auto flex items-center gap-1">
                Sort
                {sortOptions.map(([label, key]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={sort === key}
                    onClick={() => toggleSort(key)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md border px-2 py-1",
                      sort === key
                        ? "border-border bg-muted text-foreground"
                        : "border-transparent hover:text-foreground"
                    )}
                  >
                    {label}
                    <span aria-hidden="true">
                      {sort === key ? (direction === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  </button>
                ))}
              </span>
            </div>
            <ul className="space-y-3">
              {visible.map((pr) => {
                const key = pullRequestKey(pr).toLowerCase()
                return (
                  <PullRequestCard
                    key={pullRequestKey(pr)}
                    pr={pr}
                    login={login}
                    review={
                      reviews.isError ? null : (
                        <PullRequestReview
                          summary={reviews.data?.[key]}
                          pending={reviews.isPending}
                          known={Object.hasOwn(reviews.data ?? {}, key)}
                        />
                      )
                    }
                    onRemoved={() => {
                      forgetPullRequest(queryClient, login, pr)
                      if (visible.length === 1 && page > 0)
                        onFiltersChange({ page: page - 1 || undefined }, true)
                    }}
                    onReady={() => refreshPullRequest(queryClient, login, pr)}
                  />
                )
              })}
              {visible.length === 0 && (
                <li className="rounded-lg border border-border bg-card px-4 py-12 text-center text-xs text-muted-foreground">
                  {detailsLoading || query.isFetchingNextPage
                    ? "Loading matching PRs…"
                    : all.length
                      ? "No PRs match these filters."
                      : "No open PRs found."}
                </li>
              )}
            </ul>
            <div className="flex items-center gap-3 text-xs">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 0 || refreshing}
                onClick={() => onFiltersChange({ page: page - 1 || undefined })}
              >
                Prev
              </Button>
              <span>Page {page + 1}</span>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  ((page + 1) * pageSize >= filtered.length &&
                    !query.hasNextPage) ||
                  query.isFetching
                }
                onClick={() => onFiltersChange({ page: page + 1 })}
              >
                Next
              </Button>
              {query.isFetchingNextPage ? (
                <span role="status">Loading more PRs from GitHub…</span>
              ) : (
                detailsLoading && <span role="status">Loading PR details…</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {visible.length} of {filtered.length}
              {query.hasNextPage ? "+" : ""} PRs · Added/deleted lines include
              tests and docs. PRs with checks still running are Pending.
            </p>
          </>
        )
      )}
    </section>
  )
}
