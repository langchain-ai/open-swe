import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { BugBeetleIcon, FlagIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { MultiSelect } from "@/components/ui/multi-select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type OpenPullRequest, type ReviewSummary } from "@/lib/api"
import { cn } from "@/lib/utils"
import { reviewStatuses, type ReviewsSearch, type ReviewSort } from "./search"

const control =
  "rounded-md border border-border bg-background px-3 py-2 text-xs text-foreground"

function overallStatus(pr: OpenPullRequest) {
  if (pr.detailsLoading) return "Loading…"
  if (pr.detailsError) return "Could not load status"
  if (pr.draft) return "Draft"
  if (pr.mergeable === false || pr.mergeState === "dirty") return "Conflicted"
  if (pr.ci === "failing") return "Failing"
  if (
    !pr.statusAvailable ||
    pr.mergeable === null ||
    pr.ci === "unknown" ||
    pr.reviewDecision === null
  )
    return "Status unavailable"
  if (pr.reviewDecision === "changes_requested") return "Changes Requested"
  if (pr.reviewDecision === "approved") return "Approved"
  return "Reviewable"
}

function FixPullRequest({ pr }: { pr: OpenPullRequest }) {
  const fix = useMutation({
    mutationFn: () => api.fixPullRequest(pr),
  })
  return (
    <div className="mt-2">
      <Button
        size="sm"
        variant="outline"
        disabled={fix.isPending || fix.isSuccess}
        aria-live="polite"
        onClick={() => fix.mutate()}
      >
        {fix.isPending
          ? "Queuing fix…"
          : fix.isSuccess
            ? "Fix queued"
            : fix.isError
              ? "Retry fix"
              : "Fix in Open SWE"}
      </Button>
      {fix.error && (
        <p role="alert" className="mt-1 text-destructive">
          {fix.error.message}
        </p>
      )}
    </div>
  )
}

function ReviewIndicators({ review }: { review: ReviewSummary }) {
  return (
    <a
      href={`/agents/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`}
      className="inline-flex flex-col gap-1.5 hover:underline"
      aria-label={`Open review: ${review.counts.bugs} bugs, ${review.counts.flags} flags`}
    >
      <span className="flex gap-3">
        <span
          className={cn(
            "inline-flex items-center gap-1",
            review.counts.bugs > 0 && "text-destructive"
          )}
        >
          <BugBeetleIcon aria-hidden="true" className="size-3.5" />
          {review.counts.bugs} bugs
        </span>
        <span className="inline-flex items-center gap-1">
          <FlagIcon aria-hidden="true" className="size-3.5" />
          {review.counts.flags} flags
        </span>
      </span>
      {review.status === "running" && (
        <span className="text-amber-700 dark:text-amber-400">Reviewing…</span>
      )}
      {review.status === "error" && (
        <span className="text-destructive">Review failed</span>
      )}
    </a>
  )
}

function dateLabel(value: string | null) {
  return value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Unavailable"
}

function Diffstat({ pr }: { pr: OpenPullRequest }) {
  if (pr.detailsLoading) return <Skeleton className="h-4 w-20" />
  if (pr.additions === null || pr.deletions === null) return <span>—</span>
  const total = pr.additions + pr.deletions
  return (
    <div
      className="min-w-24"
      aria-label={`${pr.additions} lines added, ${pr.deletions} lines deleted`}
    >
      <div className="flex gap-2 font-mono text-xs tabular-nums">
        <span className="text-emerald-600 dark:text-emerald-400">
          +{pr.additions.toLocaleString()}
        </span>
        <span className="text-destructive">
          −{pr.deletions.toLocaleString()}
        </span>
      </div>
      <div
        className="mt-1.5 flex h-1 w-20 overflow-hidden rounded-full bg-muted"
        aria-hidden="true"
      >
        {total > 0 && (
          <>
            <span
              className="bg-emerald-500"
              style={{ width: `${(pr.additions / total) * 100}%` }}
            />
            <span
              className="bg-destructive"
              style={{ width: `${(pr.deletions / total) * 100}%` }}
            />
          </>
        )}
      </div>
    </div>
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
    page = 0,
    direction = "asc",
  } = filters
  const toggleSort = (next: ReviewSort) =>
    onFiltersChange({
      page: undefined,
      sort: next,
      direction: sort === next && direction === "asc" ? "desc" : "asc",
    })
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ["my-pull-requests", login, repo, sort, direction],
    queryFn: () => api.myPullRequests(repo.join(","), sort, direction),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const repos = useQuery({
    queryKey: ["my-pull-requests", login, [], "updatedAt", "asc"],
    queryFn: () => api.myPullRequests(""),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const rows = query.data?.pullRequests ?? []
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
    : matchingRows.slice(page * 10, (page + 1) * 10)
  const detailQueries = useQueries({
    queries: requestedRows.map((pr) => ({
      queryKey: ["my-pr-details", login, pr.repo, pr.number],
      queryFn: () => api.myPullRequestDetails(pr.repo, pr.number),
      enabled: pr.detailsLoading === true,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
    })),
  })
  const all = matchingRows.flatMap((pr) => {
    const index = requestedRows.findIndex(
      (row) => row.repo === pr.repo && row.number === pr.number
    )
    const detail = detailQueries[index]
    if (detail?.data === null) return []
    if (detail?.data)
      return [
        {
          ...pr,
          ...detail.data,
          detailsLoading: false,
          detailsError: false,
          title: detail.data.title || pr.title,
          createdAt: detail.data.createdAt || pr.createdAt,
          updatedAt: detail.data.updatedAt || pr.updatedAt,
        },
      ]
    return [
      {
        ...pr,
        detailsLoading: pr.detailsLoading && !detail?.isError,
        detailsError: detail?.isError,
      },
    ]
  })
  const filtered = all.filter(
    (pr) =>
      !filter?.length || filter.some((status) => overallStatus(pr) === status)
  )
  const visible = filtered.slice(page * 10, (page + 1) * 10)
  const detailsLoading = detailQueries.some((detail) => detail.isFetching)
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
  const repoNames = [
    ...new Set([
      ...all.map((pr) => pr.repo),
      ...(repos.data?.pullRequests ?? []).map((pr) => pr.repo),
    ]),
  ].sort()

  return (
    <section className="mt-5 space-y-4" aria-label="My open pull requests">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span aria-live="polite">
          {query.isFetching
            ? "Refreshing from GitHub…"
            : query.data
              ? `Refreshed ${dateLabel(query.data.updatedAt)}`
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
            if (repo.length) void repos.refetch()
            void queryClient.invalidateQueries({
              queryKey: ["my-pr-details", login],
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
          options={repoNames}
          value={repo}
          onValueChange={(selected) =>
            onFiltersChange({ repo: selected.length ? selected : undefined })
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
          {query.data && "Refresh failed; showing the previous snapshot. "}
          {query.error.message}
        </p>
      )}
      {reviews.error && (
        <p role="alert" className="text-xs text-destructive">
          Review indicators unavailable. {reviews.error.message}
        </p>
      )}
      {(query.data?.truncated || query.data?.incomplete) && (
        <p role="status" className="text-xs text-amber-700 dark:text-amber-400">
          {query.data.truncated
            ? "Showing the first 100 matching open PRs in the selected order. Filter by repository to narrow the list. "
            : ""}
          {query.data.incomplete &&
            "GitHub returned incomplete search results. Refresh to try again."}
        </p>
      )}
      {query.isLoading ? (
        <Skeleton className="h-56 w-full" />
      ) : (
        query.data && (
          <>
            <div className="overflow-x-auto rounded-lg border border-border bg-card">
              <table className="w-full text-left text-xs">
                <thead className="border-b border-border bg-muted/30 text-muted-foreground">
                  <tr>
                    {(
                      [
                        ["PR", null],
                        ["Pull request", null],
                        ["Diffstat", null],
                        ["Status", null],
                        ["Review issues", null],
                        ["Last updated", "updatedAt"],
                        ["Created", "createdAt"],
                      ] as const
                    ).map(([label, key]) => (
                      <th
                        key={label}
                        scope="col"
                        aria-sort={
                          key
                            ? sort === key
                              ? direction === "asc"
                                ? "ascending"
                                : "descending"
                              : "none"
                            : undefined
                        }
                        className="px-4 py-3 font-medium whitespace-nowrap"
                      >
                        {key ? (
                          <button
                            type="button"
                            onClick={() => toggleSort(key)}
                            className="inline-flex items-center gap-1 hover:text-foreground"
                          >
                            {label}
                            <span aria-hidden="true">
                              {sort === key
                                ? direction === "asc"
                                  ? "↑"
                                  : "↓"
                                : "↕"}
                            </span>
                          </button>
                        ) : (
                          label
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {visible.map((pr) => (
                    <tr
                      key={`${pr.repo}#${pr.number}`}
                      className="align-top hover:bg-muted/20"
                    >
                      <td className="px-4 py-4 font-mono tabular-nums">
                        <a
                          href={`https://github.com/${pr.repo}/pull/${pr.number}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground hover:text-foreground hover:underline"
                        >
                          #{pr.number}
                        </a>
                      </td>
                      <td className="max-w-md min-w-64 px-4 py-4">
                        <a
                          href={`https://github.com/${pr.repo}/pull/${pr.number}`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium hover:underline"
                        >
                          {pr.title}
                        </a>
                        <div className="mt-1 text-muted-foreground">
                          {pr.repo}
                          {pr.draft && " · Draft"}
                        </div>
                        {pr.headRef && (
                          <div
                            className="mt-1 truncate font-mono text-[11px] text-muted-foreground"
                            title={pr.headRef}
                          >
                            {pr.headRef}
                          </div>
                        )}
                        {!pr.statusAvailable && !pr.detailsLoading && (
                          <p className="mt-1 text-amber-700 dark:text-amber-400">
                            Live PR status unavailable
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-4">
                        <Diffstat pr={pr} />
                      </td>
                      <td className="min-w-44 px-4 py-4">
                        <span
                          className={cn(
                            "font-medium",
                            [
                              "Conflicted",
                              "Failing",
                              "Changes Requested",
                            ].includes(overallStatus(pr)) && "text-destructive",
                            overallStatus(pr) === "Approved" &&
                              "text-emerald-600 dark:text-emerald-400"
                          )}
                        >
                          {overallStatus(pr)}
                        </span>
                        {pr.failingChecks.length > 0 && (
                          <ul className="mt-1 space-y-1 text-destructive">
                            {pr.failingChecks.slice(0, 3).map((name, index) => (
                              <li key={`${name}-${index}`}>{name}</li>
                            ))}
                          </ul>
                        )}
                        {pr.failingChecks.length > 3 && (
                          <details className="mt-1 text-destructive">
                            <summary className="cursor-pointer">
                              +{pr.failingChecks.length - 3} more
                            </summary>
                            <ul className="mt-1 space-y-1">
                              {pr.failingChecks.slice(3).map((name, index) => (
                                <li key={`${name}-${index}`}>{name}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                        {pr.pendingChecks.length > 0 && (
                          <details className="mt-1 text-muted-foreground">
                            <summary className="cursor-pointer">
                              {pr.pendingChecks.length} pending
                            </summary>
                            <ul className="mt-1">
                              {pr.pendingChecks.map((name, index) => (
                                <li key={`${name}-${index}`}>{name}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                        {["Conflicted", "Failing"].includes(
                          overallStatus(pr)
                        ) && <FixPullRequest pr={pr} />}
                      </td>
                      <td className="min-w-36 px-4 py-4 text-muted-foreground">
                        {reviews.error ? (
                          "Unavailable"
                        ) : reviews.data?.[
                            `${pr.repo}#${pr.number}`.toLowerCase()
                          ] ? (
                          <ReviewIndicators
                            review={
                              reviews.data[
                                `${pr.repo}#${pr.number}`.toLowerCase()
                              ]!
                            }
                          />
                        ) : reviews.isPending ? (
                          "Loading review…"
                        ) : !Object.hasOwn(
                            reviews.data ?? {},
                            `${pr.repo}#${pr.number}`.toLowerCase()
                          ) ? (
                          "Unavailable"
                        ) : (
                          "Not reviewed"
                        )}
                      </td>
                      <td className="px-4 py-4 whitespace-nowrap text-muted-foreground">
                        <time
                          dateTime={pr.updatedAt ?? undefined}
                          title={pr.updatedAt ?? undefined}
                        >
                          {dateLabel(pr.updatedAt)}
                        </time>
                      </td>
                      <td className="px-4 py-4 whitespace-nowrap text-muted-foreground">
                        <time
                          dateTime={pr.createdAt ?? undefined}
                          title={pr.createdAt ?? undefined}
                        >
                          {dateLabel(pr.createdAt)}
                        </time>
                      </td>
                    </tr>
                  ))}
                  {visible.length === 0 && (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-4 py-12 text-center text-muted-foreground"
                      >
                        {detailsLoading
                          ? "Loading matching PRs…"
                          : all.length
                            ? "No PRs match these filters."
                            : "No open PRs found."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 0 || query.isFetching}
                onClick={() => onFiltersChange({ page: page - 1 || undefined })}
              >
                Prev
              </Button>
              <span>Page {page + 1}</span>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  (page + 1) * 10 >= filtered.length || query.isFetching
                }
                onClick={() => onFiltersChange({ page: page + 1 })}
              >
                Next
              </Button>
              {detailsLoading && <span role="status">Loading PR details…</span>}
            </div>
            <p className="text-xs text-muted-foreground">
              {visible.length} of {filtered.length} PRs · Added/deleted lines
              include tests and docs. Reviewable means ready for human review;
              pending checks are shown above.
            </p>
          </>
        )
      )}
    </section>
  )
}
