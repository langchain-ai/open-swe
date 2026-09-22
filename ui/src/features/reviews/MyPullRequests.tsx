import { useQueries, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { MultiSelect } from "@/components/ui/multi-select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type OpenPullRequest, type ReviewSummary } from "@/lib/api"
import { prioritizeRepositories } from "@/lib/repositoryUsage"
import { useProfile, useRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"
import {
  PullRequestCard,
  type PullRequestOutcome,
} from "./components/PullRequestCard"
import { PullRequestDetail } from "./components/PullRequestDetail"
import { PullRequestList } from "./components/PullRequestList"
import { PullRequestReview } from "./components/PullRequestReview"
import { refreshPullRequest } from "./lib/cache"
import { dateLabel } from "./lib/dateLabel"
import { pullRequestKey, statusLabels } from "./lib/status"
import { control } from "./lib/styles"
import { useOpenPullRequests } from "./lib/useOpenPullRequests"
import { usePullRequestDetails } from "./lib/usePullRequestDetails"
import { reviewStatuses, type ReviewsSearch, type ReviewSort } from "./search"

const chunkSize = 10

const sortOptions = [
  ["Last updated", "updatedAt"],
  ["Created", "createdAt"],
] as const

function chunked(refs: { repo: string; number: number }[]) {
  return Array.from(
    { length: Math.ceil(refs.length / chunkSize) },
    (_, index) => refs.slice(index * chunkSize, (index + 1) * chunkSize)
  )
}

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
    direction = "desc",
    pr: selected,
  } = filters
  const toggleSort = (next: ReviewSort) =>
    onFiltersChange({
      sort: next,
      direction: sort === next && direction === "asc" ? "desc" : "asc",
    })
  const queryClient = useQueryClient()
  const query = useOpenPullRequests(login, repo, sort, direction)
  const knownRepos = useRepos()
  const profile = useProfile()
  // Rows whose details have been asked for. Tied to the filter set that grew
  // it, so changing a filter starts the list over without an extra render.
  const [growth, setGrowth] = useState({ key: "", rows: chunkSize })
  // A merged or closed PR keeps its row until the next refresh: dropping it
  // immediately would pull every card below it up under the pointer.
  const [settled, setSettled] = useState<Record<string, PullRequestOutcome>>({})
  const pages = query.data?.pages ?? []
  const latest = pages.at(-1)
  const rows = pages.flatMap((loaded) => loaded.pullRequests)
  const { fetchNextPage, isFetching } = query
  const listKey = [
    repo.join(","),
    search,
    (filter ?? []).join(","),
    sort,
    direction,
  ].join("|")
  const requested = growth.key === listKey ? growth.rows : chunkSize
  // A search or a status filter is matched against loaded rows only, so both
  // have to pull the rest of the pages in before they can answer at all.
  const needsMorePages =
    query.hasNextPage &&
    (Boolean(filter?.length) || Boolean(search) || requested >= rows.length)
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
  // A status filter can only be applied to rows whose details have arrived, so
  // it costs a detail read for every row rather than only the ones on screen.
  const requestedRows = filter?.length
    ? matchingRows
    : matchingRows.slice(0, requested)
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
  const visible = filter?.length ? filtered : filtered.slice(0, requested)
  const refreshing = query.isFetching && !query.isFetchingNextPage
  const incomplete = pages.some((loaded) => loaded.incomplete)
  const reviewChunks = chunked(
    visible.map((pr) => ({ repo: pr.repo, number: pr.number }))
  )
  const reviewQueries = useQueries({
    queries: reviewChunks.map((refs) => ({
      queryKey: ["my-pr-review-summaries", login, refs],
      queryFn: () => api.reviewSummaries(refs),
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
    })),
  })
  const summaries: Record<string, ReviewSummary | null> = Object.assign(
    {},
    ...reviewQueries.map((chunk) => chunk.data ?? {})
  )
  const summariesPending = reviewQueries.some((chunk) => chunk.isPending)
  const summariesUnavailable =
    reviewQueries.length > 0 && reviewQueries.every((chunk) => chunk.isError)
  const accessibleRepos = (knownRepos.data?.repositories ?? []).map(
    (known) => known.full_name
  )
  // A timed-out search returns no PRs, and filtering by repository is the way
  // out of it, so the options cannot be derived from the PRs themselves.
  const repoNames = prioritizeRepositories(
    [
      ...new Set([
        ...(accessibleRepos.length
          ? accessibleRepos
          : all.map((pr) => pr.repo)),
        ...repo,
      ]),
    ].sort(),
    profile.data?.repository_usage,
    (name) => name
  )
  // Layout follows the row actually on screen, not the search param. Filtering
  // the selected PR out closes the preview and returns the list to full width
  // instead of stranding it in rail form with no way to close.
  const selectedRow = selected
    ? all.find((row) => pullRequestKey(row) === selected)
    : undefined
  const railed = Boolean(selectedRow)

  const card = (pr: OpenPullRequest) => {
    const key = pullRequestKey(pr)
    return (
      <PullRequestCard
        pr={pr}
        login={login}
        outcome={settled[key]}
        compact={railed}
        selected={selected === key}
        onSelect={() => onFiltersChange({ pr: key })}
        review={
          summariesUnavailable ? null : (
            <PullRequestReview
              summary={summaries[key.toLowerCase()]}
              pending={summariesPending}
              known={Object.hasOwn(summaries, key.toLowerCase())}
            />
          )
        }
        onSettled={(outcome) =>
          setSettled((previous) => ({ ...previous, [key]: outcome }))
        }
        onReady={() => refreshPullRequest(queryClient, login, pr)}
      />
    )
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* Both columns grow from a zero basis, so opening a preview animates the
          list across rather than resizing it in place. */}
      <div
        className="flex min-h-0 min-w-0 flex-col transition-[flex-grow] duration-300 ease-out"
        style={{ flexGrow: 1, flexBasis: 0 }}
      >
        <section
          className="mt-4 flex min-h-0 flex-1 flex-col gap-3"
          aria-label="My open pull requests"
        >
          <div className="flex flex-wrap items-center gap-2">
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
            {!railed && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
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
            )}
            {!railed && (
              <span
                aria-live="polite"
                className="text-xs whitespace-nowrap text-muted-foreground"
              >
                {refreshing
                  ? "Refreshing from GitHub…"
                  : latest
                    ? `Refreshed ${dateLabel(latest.updatedAt)}`
                    : "Live open pull requests from GitHub"}
              </span>
            )}
            <Button
              size="sm"
              variant="outline"
              disabled={query.isFetching}
              onClick={() => {
                setSettled({})
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
                void queryClient.invalidateQueries({
                  queryKey: ["my-pr-review-summaries", login],
                })
                // An open preview outlives a refresh, so its checks and
                // comments would otherwise stay as they were when it opened.
                void queryClient.invalidateQueries({
                  queryKey: ["pr-preview"],
                })
              }}
            >
              Refresh
            </Button>
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
              GitHub&rsquo;s pull request search timed out, and the partial
              answer it returned would have hidden most of your PRs. Filter by
              repository to narrow the search, or refresh to try again.
            </p>
          ) : (
            latest && (
              <>
                {visible.length === 0 ? (
                  <p className="rounded-lg border border-border bg-card px-4 py-12 text-center text-xs text-muted-foreground">
                    {detailsLoading || query.isFetchingNextPage
                      ? "Loading matching PRs…"
                      : all.length
                        ? "No PRs match these filters."
                        : "No open PRs found."}
                  </p>
                ) : (
                  <PullRequestList
                    rows={visible}
                    available={matchingRows.length}
                    compact={railed}
                    // Unclamped: asking for more rows than are loaded is what
                    // makes the next GitHub page arrive, and reaching the end
                    // again is the only other thing that would grow it.
                    onEndReached={() =>
                      setGrowth({ key: listKey, rows: requested + chunkSize })
                    }
                  >
                    {card}
                  </PullRequestList>
                )}
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span>
                    {visible.length} of {filtered.length}
                    {query.hasNextPage ? "+" : ""} PRs
                    {!railed &&
                      " · Added/deleted lines include tests and docs. PRs with checks still running are Pending."}
                  </span>
                  {query.isFetchingNextPage ? (
                    <span role="status">Loading more PRs from GitHub…</span>
                  ) : (
                    detailsLoading && (
                      <span role="status">Loading PR details…</span>
                    )
                  )}
                </div>
              </>
            )
          )}
        </section>
      </div>

      <div
        className="flex min-h-0 min-w-0 overflow-hidden transition-[flex-grow] duration-300 ease-out"
        style={{ flexGrow: selectedRow ? 2.2 : 0, flexBasis: 0 }}
      >
        {selectedRow && (
          <div className="flex min-h-0 w-full min-w-[420px] pt-4 pl-5">
            <PullRequestDetail
              key={selected}
              pr={selectedRow}
              login={login}
              outcome={settled[pullRequestKey(selectedRow)]}
              onClose={() => onFiltersChange({ pr: undefined })}
              onSettled={(outcome) =>
                setSettled((previous) => ({
                  ...previous,
                  [pullRequestKey(selectedRow)]: outcome,
                }))
              }
              onReady={() =>
                refreshPullRequest(queryClient, login, selectedRow)
              }
            />
          </div>
        )}
      </div>
    </div>
  )
}
