import { useQueries, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Clock,
  Filter,
  GitPullRequest,
  RefreshCw,
  Search,
} from "@/components/glyphs"
import { MultiSelect } from "@/components/MultiSelect"
import { api, type OpenPullRequest, type ReviewSummary } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { expiresInBrowser } from "@/lib/query"
import { cn } from "@/lib/utils"
import {
  PullRequestCard,
  type PullRequestOutcome,
} from "./components/PullRequestCard"
import { PullRequestDetail } from "./components/PullRequestDetail"
import { PullRequestList } from "./components/PullRequestList"
import { PullRequestReview } from "./components/PullRequestReview"
import { pullRequestPreviewQuery, refreshPullRequest } from "./lib/cache"
import { dateLabel } from "./lib/dateLabel"
import { pullRequestKey, statusLabels } from "./lib/status"
import { useOpenPullRequests } from "./lib/useOpenPullRequests"
import { usePullRequestDetails } from "./lib/usePullRequestDetails"
import { usePullRequestSearch } from "./lib/usePullRequestSearch"
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
  const descriptionSearch = usePullRequestSearch(search)
  const descriptionMatches = new Set(
    descriptionSearch.data?.pages.flatMap((page) =>
      page.pull_requests.map((pr) => pullRequestKey(pr).toLowerCase())
    ) ?? []
  )
  const {
    hasNextPage: hasMoreMatches,
    isFetching: fetchingMatches,
    fetchNextPage: fetchMoreMatches,
  } = descriptionSearch
  useEffect(() => {
    if (hasMoreMatches && !fetchingMatches) void fetchMoreMatches()
  }, [hasMoreMatches, fetchingMatches, fetchMoreMatches])
  // Rows whose details have been asked for. Tied to the filter set that grew
  // it, so changing a filter starts the list over without an extra render.
  const [growth, setGrowth] = useState({ key: "", rows: chunkSize })
  // A merged or closed PR keeps its row until the next refresh: dropping it
  // immediately would pull every card below it up under the pointer.
  const [settled, setSettled] = useState<
    Partial<Record<string, PullRequestOutcome>>
  >({})
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
    .filter(
      (pr) =>
        `${pr.repo} #${pr.number} ${pr.title}`
          .toLowerCase()
          .includes(search.trim().toLowerCase()) ||
        descriptionMatches.has(pullRequestKey(pr).toLowerCase())
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
      ...expiresInBrowser,
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
  const repoNames = [
    ...new Set([
      ...(accessibleRepos.length ? accessibleRepos : all.map((pr) => pr.repo)),
      ...repo,
    ]),
  ].sort()
  // Layout follows the row actually on screen, not the search param. Filtering
  // the selected PR out closes the preview and returns the list to full width
  // instead of stranding it in rail form with no way to close.
  const selectedRow = selected
    ? all.find((row) => pullRequestKey(row) === selected)
    : undefined
  const railed = Boolean(selectedRow)
  const selectedIndex = visible.findIndex(
    (row) => pullRequestKey(row) === selected
  )
  const nextRow = selectedIndex >= 0 ? visible[selectedIndex + 1] : undefined
  useEffect(() => {
    if (nextRow)
      void queryClient.prefetchQuery(pullRequestPreviewQuery(nextRow))
  }, [queryClient, nextRow])

  const card = (pr: OpenPullRequest) => {
    const key = pullRequestKey(pr)
    return (
      <PullRequestCard
        pr={pr}
        login={login}
        outcome={settled[key]}
        compact={railed}
        selected={selected === key}
        onSelect={() => {
          refreshPullRequest(queryClient, login, pr)
          onFiltersChange({ pr: key })
        }}
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

  const refresh = () => {
    setSettled({})
    queryClient.removeQueries({
      queryKey: ["my-pr-details", login],
      predicate: (cached) => cached.state.data === null,
    })
    void query.refetch()
    void queryClient.invalidateQueries({
      queryKey: ["pull-request-search", login],
    })
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
  }
  const listLoading =
    detailsLoading || query.isFetchingNextPage || descriptionSearch.isSearching

  return (
    <Inline align="stretch" className="min-h-0 flex-1">
      {/* Both columns grow from a zero basis, so opening a preview animates the
          list across rather than resizing it in place. */}
      <Stack
        className="min-h-0 min-w-0 transition-[flex-grow] duration-300 ease-out-quint motion-reduce:transition-none"
        style={{ flexGrow: 1, flexBasis: 0 }}
      >
        <Stack
          render={<section aria-label="My open pull requests" />}
          gap="md"
          className="min-h-0 flex-1"
        >
          <Inline gap="sm" wrap>
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
            <SearchInput
              label="Search pull requests"
              placeholder="Search title, description, or PR number…"
              className="min-w-40 flex-1"
              value={search}
              onValueChange={(value) =>
                onFiltersChange({ q: value || undefined }, true)
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
              <Inline gap="xs" className="text-meta text-ink-subtle">
                <span className="pr-1">Sort</span>
                {sortOptions.map(([label, key]) => (
                  <Button
                    key={key}
                    size="compact"
                    variant={sort === key ? "outline" : "ghost"}
                    aria-pressed={sort === key}
                    onClick={() => toggleSort(key)}
                    className={sort === key ? undefined : "text-ink-subtle"}
                  >
                    {label}
                    <Icon
                      icon={
                        sort === key
                          ? direction === "asc"
                            ? ArrowUp
                            : ArrowDown
                          : ArrowUpDown
                      }
                      size="sm"
                      className="text-ink-subtle"
                    />
                  </Button>
                ))}
              </Inline>
            )}
            <Inline gap="sm" className="ml-auto">
              {!railed && (
                <span
                  aria-live="polite"
                  className="text-meta whitespace-nowrap text-ink-subtle"
                >
                  {refreshing
                    ? "Refreshing from GitHub…"
                    : latest
                      ? `Refreshed ${dateLabel(latest.updatedAt)}`
                      : "Live open pull requests from GitHub"}
                </span>
              )}
              <Button
                size="compact"
                variant="outline"
                disabled={query.isFetching}
                onClick={refresh}
              >
                <Icon
                  icon={RefreshCw}
                  size="sm"
                  className={cn(
                    refreshing && "animate-spin motion-reduce:animate-none"
                  )}
                />
                Refresh
              </Button>
            </Inline>
          </Inline>
          {query.error && (
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title={
                !latest
                  ? "Could not load your pull requests"
                  : query.isFetchNextPageError
                    ? "Could not load more PRs"
                    : "Refresh failed"
              }
              description={
                (latest
                  ? query.isFetchNextPageError
                    ? "Showing the pages loaded so far. "
                    : "Showing the previous snapshot. "
                  : "") + query.error.message
              }
            />
          )}
          {search.trim() && descriptionSearch.isError && (
            <StateNotice
              tone="INFO"
              icon={Search}
              title="Description search is unavailable"
              description="Showing repository, title, and PR number matches only."
            />
          )}
          {query.isLoading ? (
            <Stack gap="md">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-40 w-full rounded-panel" />
              ))}
            </Stack>
          ) : incomplete ? (
            <StateNotice
              tone="ATTENTION"
              icon={Clock}
              title="GitHub's pull request search timed out"
              description="The partial answer it returned would have hidden most of your PRs. Filter by repository to narrow the search, or refresh to try again."
            />
          ) : (
            latest && (
              <>
                {visible.length === 0 ? (
                  listLoading ? (
                    <Inline
                      role="status"
                      gap="sm"
                      justify="center"
                      bg="panel"
                      border="line"
                      radius="panel"
                      className="py-12 text-meta text-ink-subtle"
                    >
                      <Spinner size="sm" />
                      Loading matching PRs…
                    </Inline>
                  ) : all.length ? (
                    <EmptyState
                      icon={Filter}
                      title="No PRs match these filters."
                      description="Clear a filter or the search to see the rest."
                    />
                  ) : (
                    <EmptyState
                      icon={GitPullRequest}
                      title="No open PRs found."
                      description="Pull requests you open on GitHub appear here."
                    />
                  )
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
                <Inline gap="md" className="text-meta text-ink-subtle">
                  <span className="tabular-nums">
                    {visible.length} of {filtered.length}
                    {query.hasNextPage ? "+" : ""} PRs
                    {!railed &&
                      " · Added/deleted lines include tests and docs. PRs with checks still running are Pending."}
                  </span>
                  {query.isFetchingNextPage ? (
                    <Inline role="status" gap="xs">
                      <Spinner size="sm" />
                      Loading more PRs from GitHub…
                    </Inline>
                  ) : (
                    detailsLoading && (
                      <Inline role="status" gap="xs">
                        <Spinner size="sm" />
                        Loading PR details…
                      </Inline>
                    )
                  )}
                </Inline>
              </>
            )
          )}
        </Stack>
      </Stack>

      <Inline
        align="stretch"
        className="min-h-0 min-w-0 overflow-hidden transition-[flex-grow] duration-300 ease-out-quint motion-reduce:transition-none"
        style={{ flexGrow: selectedRow ? 2.2 : 0, flexBasis: 0 }}
      >
        {selectedRow && (
          <Box className="flex min-h-0 w-full min-w-105 pl-5">
            <PullRequestDetail
              key={selected}
              pr={selectedRow}
              login={login}
              outcome={settled[pullRequestKey(selectedRow)]}
              onClose={() => onFiltersChange({ pr: undefined })}
              expandedFiles={filters.files}
              scrollAnchor={filters.at}
              onPositionChange={(changes) => onFiltersChange(changes, true)}
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
          </Box>
        )}
      </Inline>
    </Inline>
  )
}
