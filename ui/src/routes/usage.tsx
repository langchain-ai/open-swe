import { createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowClockwiseIcon,
  CaretDownIcon,
  CheckCircleIcon,
  ClockCountdownIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"
import {
  ArrowDownNarrowWide,
  ArrowUpNarrowWide,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
} from "lucide-react"
import { Fragment, useState } from "react"

import type {
  AnalyticsMetadata,
  PRMergeRateCohort,
  ReviewerStatsPayload,
  SortDirection,
  UsageLeaderboardPeriod,
  UsageLeaderboardRow,
  UsageLeaderboardSort,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { api, ApiError } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { safeModelLabel } from "@/lib/modelLabel"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/usage")({
  validateSearch: (search: Record<string, unknown>) => ({
    period: typeof search.period === "string" ? search.period : undefined,
  }),
  component: UsagePage,
})

const PAGE_SIZES = [10, 25, 50, 100] as const

interface SortableColumn<Key extends string> {
  key: Key
  label: string
  align: "left" | "right"
  defaultDirection?: SortDirection
  tooltip?: string
}

type UsageScope = "invocations" | "threads"
type PROutcomesSort =
  | "model"
  | "prs_opened"
  | "merged"
  | "closed_without_merge"
  | "open"
  | "median_distance"
  | "merge_rate"
  | "avg_delivery_seconds"
  | "avg_merge_seconds"

const PERIOD_LABELS: Record<UsageLeaderboardPeriod, string> = {
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
}

function UsagePage() {
  const session = useSession()
  const period =
    (Route.useSearch().period as UsageLeaderboardPeriod | undefined) ?? "7d"
  const navigate = Route.useNavigate()
  const activePeriod: UsageLeaderboardPeriod = ["7d", "30d", "all"].includes(
    period
  )
    ? period
    : "7d"

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Usage"
      className="max-w-5xl"
      action={
        <UsageDateRange
          period={activePeriod}
          onPeriodChange={(value) =>
            navigate({ to: "/usage", search: { period: value } })
          }
        />
      }
    >
      <UsageAnalytics
        period={activePeriod}
        login={session.data.login}
        isAdmin={session.data.is_admin}
      />
    </AppShell>
  )
}

export function UsageDateRange({
  period: activePeriod,
  onPeriodChange,
}: {
  period: UsageLeaderboardPeriod
  onPeriodChange: (period: UsageLeaderboardPeriod) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <label htmlFor="usage-date-range" className="text-sm font-medium">
        Date range
      </label>
      <Select
        value={activePeriod}
        onValueChange={(value) =>
          onPeriodChange(value as UsageLeaderboardPeriod)
        }
      >
        <SelectTrigger id="usage-date-range" className="w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(PERIOD_LABELS).map(([value, label]) => (
            <SelectItem key={value} value={value}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

export function UsageAnalytics({
  period: activePeriod,
  login,
  isAdmin,
}: {
  period: UsageLeaderboardPeriod
  login: string
  isAdmin: boolean
}) {
  const [leaderboardPageSize, setLeaderboardPageSize] = useState(10)

  return (
    <UsageAnalyticsPeriod
      key={activePeriod}
      period={activePeriod}
      login={login}
      isAdmin={isAdmin}
      pageSize={leaderboardPageSize}
      onPageSizeChange={setLeaderboardPageSize}
    />
  )
}

function UsageAnalyticsPeriod({
  period: activePeriod,
  login,
  isAdmin,
  pageSize: leaderboardPageSize,
  onPageSizeChange: setLeaderboardPageSize,
}: {
  period: UsageLeaderboardPeriod
  login: string
  isAdmin: boolean
  pageSize: number
  onPageSizeChange: (pageSize: number) => void
}) {
  const [leaderboardPage, setLeaderboardPage] = useState(1)
  const [sort, setSort] = useState<UsageLeaderboardSort>("rank")
  const [direction, setDirection] = useState<SortDirection>("asc")
  const [usageScope, setUsageScope] = useState<UsageScope>("invocations")
  const [leaderboardCursors, setLeaderboardCursors] = useState<
    (string | undefined)[]
  >([undefined])
  const leaderboard = useQuery({
    queryKey: [
      "usageLeaderboard",
      activePeriod,
      login,
      isAdmin,
      leaderboardPage,
      leaderboardPageSize,
      leaderboardCursors[leaderboardPage - 1],
      sort,
      direction,
    ],
    queryFn: () =>
      api.usageLeaderboard(
        activePeriod,
        leaderboardPageSize,
        leaderboardCursors[leaderboardPage - 1],
        sort,
        direction
      ),
    placeholderData: (previousData, previousQuery) =>
      previousQuery?.queryKey[2] === login &&
      previousQuery.queryKey[3] === isAdmin
        ? previousData
        : undefined,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status >= 400) && count < 2,
  })
  const report = usePRMergeRateReport(activePeriod, login, isAdmin)
  const refreshing = leaderboard.isFetching || report.isFetching
  const refreshNow = () => {
    void leaderboard.refetch()
    void report.refetch()
  }
  const metadata = [
    leaderboard.isError ? undefined : leaderboard.data,
    report.isError ? undefined : report.data,
  ].filter((data): data is NonNullable<typeof data> => data != null)

  return (
    <>
      <PRMergeRateSection report={report} />

      <SettingsSection
        title="Agent leaderboard"
        description="Ranked by merged PRs, then agent lines of code and PRs opened."
        action={
          <div className="flex items-center gap-2">
            <div
              className="flex rounded-md bg-muted p-0.5"
              role="group"
              aria-label="Usage scope"
            >
              {(["invocations", "threads"] as const).map((scope) => (
                <Button
                  key={scope}
                  type="button"
                  size="sm"
                  variant={usageScope === scope ? "secondary" : "ghost"}
                  aria-pressed={usageScope === scope}
                  className="capitalize"
                  onClick={() => {
                    setUsageScope(scope)
                    if (sort === "invocations" || sort === "threads") {
                      setSort(scope)
                    } else if (
                      sort === "avg_invocation_seconds" ||
                      sort === "avg_thread_seconds"
                    ) {
                      setSort(
                        scope === "threads"
                          ? "avg_thread_seconds"
                          : "avg_invocation_seconds"
                      )
                    }
                    setLeaderboardPage(1)
                    setLeaderboardCursors([undefined])
                  }}
                >
                  {scope}
                </Button>
              ))}
            </div>
          </div>
        }
      >
        {leaderboard.isLoading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : leaderboard.isError ? (
          <div className="space-y-2 p-4 text-xs" role="alert">
            <p className="text-destructive">
              {leaderboard.error instanceof ApiError &&
              leaderboard.error.status === 503
                ? "Usage analytics is unavailable on this deployment."
                : "Could not load usage analytics. Try again."}
            </p>
            <button
              type="button"
              className="underline"
              onClick={() => leaderboard.refetch()}
            >
              Retry usage analytics
            </button>
          </div>
        ) : !leaderboard.data?.total_members ? (
          <div className="p-6 text-center text-xs text-muted-foreground">
            No Open SWE Agent usage has been recorded for{" "}
            {PERIOD_LABELS[activePeriod].toLowerCase()} yet.
          </div>
        ) : (
          <UsageTable
            scope={usageScope}
            period={activePeriod}
            currentUserRank={leaderboard.data.current_user_rank}
            rows={leaderboard.data.rows}
            totalMembers={leaderboard.data.total_members}
            page={leaderboardPage}
            pageSize={leaderboardPageSize}
            sort={sort}
            direction={direction}
            isUpdating={leaderboard.isPlaceholderData}
            onSort={(nextSort, nextDirection) => {
              setDirection(
                sort === nextSort
                  ? direction === "asc"
                    ? "desc"
                    : "asc"
                  : nextDirection
              )
              setSort(nextSort)
              setLeaderboardPage(1)
              setLeaderboardCursors([undefined])
            }}
            onPageChange={(page) => {
              const nextCursor = leaderboard.data.next_cursor
              if (page > leaderboardPage && nextCursor) {
                setLeaderboardCursors((cursors) => {
                  const updated = cursors.slice(0, leaderboardPage)
                  updated[leaderboardPage] = nextCursor
                  return updated
                })
              }
              setLeaderboardPage(page)
            }}
            onPageSizeChange={(pageSize) => {
              setLeaderboardPageSize(pageSize)
              setLeaderboardPage(1)
              setLeaderboardCursors([undefined])
            }}
          />
        )}
      </SettingsSection>

      <SettingsSection
        title="Reviewer stats"
        description="Issues surfaced by Open SWE Review and how often users addressed them."
      >
        {leaderboard.isLoading ? (
          <div className="grid gap-3 p-4 sm:grid-cols-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : leaderboard.isError ? (
          <p className="p-4 text-xs text-destructive">
            Reviewer stats are unavailable. Retry usage analytics above.
          </p>
        ) : leaderboard.data?.reviewer_stats ? (
          <ReviewerStats stats={leaderboard.data.reviewer_stats} />
        ) : (
          <div className="p-6 text-center text-xs text-muted-foreground">
            No reviewer stats have been recorded for{" "}
            {PERIOD_LABELS[activePeriod].toLowerCase()} yet.
          </div>
        )}
      </SettingsSection>
      <AnalyticsCoverage
        reports={metadata}
        refreshing={refreshing}
        onRefresh={refreshNow}
      />
    </>
  )
}

function AnalyticsCoverage({
  reports,
  refreshing,
  onRefresh,
}: {
  reports: AnalyticsMetadata[]
  refreshing: boolean
  onRefresh: () => void
}) {
  if (!reports.length) return null
  const latest = reports.reduce((a, b) => (a.as_of > b.as_of ? a : b))
  const hasPendingEvents = reports.some((data) => data.has_pending_events)
  const hasFailedEvents = reports.some((data) => data.has_failed_events)
  const status: {
    label: string
    description?: string
    icon: typeof WarningCircleIcon
    tone: string
  } = hasFailedEvents
    ? {
        label: "Analytics need attention",
        description:
          "Some events could not be processed. Reports may be incomplete.",
        icon: WarningCircleIcon,
        tone: "text-destructive",
      }
    : hasPendingEvents
      ? {
          label: "Analytics are updating",
          description: "New activity is still being processed.",
          icon: ClockCountdownIcon,
          tone: "text-amber-600 dark:text-amber-400",
        }
      : {
          label: "Analytics are up to date",
          icon: CheckCircleIcon,
          tone: "text-emerald-600 dark:text-emerald-400",
        }
  const StatusIcon = status.icon

  return (
    <div role="status" aria-label="Analytics coverage">
      <details className="group rounded-xl border border-border bg-card text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-3 rounded-xl px-4 py-3.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          <StatusIcon
            aria-hidden="true"
            className={`size-4 shrink-0 ${status.tone}`}
            weight="fill"
          />
          <span className="min-w-0 flex-1">
            <span className="block font-medium text-foreground">
              {status.label}
            </span>
            {status.description ? (
              <span className="mt-0.5 block text-muted-foreground">
                {status.description}
              </span>
            ) : null}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shrink-0"
            disabled={refreshing}
            onClick={(event) => {
              event.preventDefault()
              onRefresh()
            }}
          >
            <ArrowClockwiseIcon
              aria-hidden="true"
              className={`size-3.5 ${refreshing ? "animate-spin" : ""}`}
            />
            {refreshing ? "Refreshing…" : "Refresh now"}
          </Button>
          <span className="flex shrink-0 items-center gap-1 font-medium text-muted-foreground group-open:text-foreground">
            Details
            <CaretDownIcon
              aria-hidden="true"
              className="size-3.5 transition-transform group-open:rotate-180"
            />
          </span>
        </summary>
        <div className="space-y-1 border-t border-border px-4 py-3 text-muted-foreground">
          <p>
            Reporting since{" "}
            <time dateTime={latest.reporting_cutover_at}>
              {new Date(latest.reporting_cutover_at).toLocaleString()}
            </time>
            .
          </p>
          <p>
            {latest.last_processed_at
              ? `Last event processed ${new Date(latest.last_processed_at).toLocaleString()}. `
              : "No events have been processed yet. "}
            {hasPendingEvents
              ? "Some captured events are still waiting to be processed. "
              : ""}
            {hasFailedEvents
              ? "Some events could not be processed. Reports may be incomplete. "
              : ""}
            Reports checked {new Date(latest.as_of).toLocaleString()}.
          </p>
        </div>
      </details>
    </div>
  )
}

function usePRMergeRateReport(
  period: UsageLeaderboardPeriod,
  login: string,
  isAdmin: boolean
) {
  return useQuery({
    queryKey: ["prMergeRateByModel", period, login, isAdmin],
    queryFn: () => api.prMergeRateByModel(period),
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status >= 400) && count < 2,
  })
}

function PRMergeRateSection({
  report,
}: {
  report: ReturnType<typeof usePRMergeRateReport>
}) {
  const data = report.isError ? undefined : report.data
  const emptyMessage =
    data?.status === "not_started"
      ? "No analytics records have been captured since the reporting cutover yet."
      : data?.status === "suppressed"
        ? "PR groups in this period are too small to show under the privacy threshold."
        : "No PRs have been recorded for this period yet."

  return (
    <SettingsSection
      title="PR outcomes"
      description="Outcomes for PRs opened during the selected period."
    >
      {report.isPending ? (
        <div
          className="space-y-2 p-4"
          role="status"
          aria-label="Loading PR outcomes"
        >
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : report.isError ? (
        <div className="space-y-2 p-4 text-xs" role="alert">
          <p className="text-destructive">
            {report.error instanceof ApiError && report.error.status === 503
              ? "PR analytics is unavailable on this deployment."
              : "Could not load PR outcomes. Try again."}
          </p>
          <button
            type="button"
            className="underline"
            onClick={() => report.refetch()}
          >
            Retry
          </button>
        </div>
      ) : data?.status === "ready" ? (
        <PRMergeRateTable
          key={data.period}
          cohorts={data.cohorts}
          maturityDays={data.maturity_days}
        />
      ) : (
        <p
          className="p-6 text-center text-xs text-muted-foreground"
          role="status"
        >
          {emptyMessage}
        </p>
      )}
      {data?.unavailable_thread_ids.length ? (
        <details className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
            Unavailable model attribution ({data.unavailable_thread_ids.length})
          </summary>
          <p className="mt-3">
            These PRs are excluded from model outcomes. Copy a thread ID to
            triage its opening-run attribution.
          </p>
          <ul className="mt-2 space-y-1">
            {data.unavailable_thread_ids.map((threadId) => (
              <li key={threadId} className="flex items-center gap-2">
                <code className="select-all">{threadId}</code>
                <button
                  type="button"
                  className="rounded-sm underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  onClick={() => void navigator.clipboard.writeText(threadId)}
                >
                  Copy
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {data ? (
        <details className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
            How these numbers work
          </summary>
          <div className="mt-3 space-y-3">
            <p>
              <strong>Open</strong> includes PRs that haven’t been merged or
              closed. Each count’s tooltip shows the age breakdown: open for
              less than {data.maturity_days} days, or open for{" "}
              {data.maturity_days} days or longer.
            </p>
            <p>
              <strong>Median distance</strong> is the median normalized line
              edit distance between each merged PR’s opening diff and final
              diff. It is calculated only for merged PRs with complete text
              patches; higher means more post-open editing.
            </p>
            <p>
              <strong>Merge rate</strong> includes only PRs old enough to have a
              meaningful outcome. It counts merged, closed without merge, and
              still-open PRs that are at least {data.maturity_days} days old.
              Newer open PRs are excluded so they do not lower the rate before
              they have had enough time to merge.
            </p>
            <p>
              <strong>Avg time to merge</strong> is the arithmetic mean of time
              from PR opened to merged across merged PRs opened in the selected
              period. Unmerged PRs are excluded, and it shows — when a group has
              no merges.
            </p>
            <p>
              <strong>Avg time to PR</strong> is the arithmetic mean of time
              from opening-run start to PR creation across all PRs opened in the
              selected period. PRs without a valid opening-run start time are
              excluded, and it shows — when a group has none.
            </p>
            <ul className="list-disc space-y-1 pl-4">
              <li>
                Merge rate = merged ÷ (merged + closed without merge + open at
                least {data.maturity_days} days).
              </li>
            </ul>
            <p>
              PRs are grouped by the model configured for the run that opened
              them. Other models may contribute through routing, fallback,
              subagents, or later runs, so these rates do not measure one
              model's independent success.
            </p>
            {data.suppression_threshold > 1 ? (
              <p>
                Groups with fewer than {data.suppression_threshold} PRs are
                hidden for privacy.
              </p>
            ) : null}
          </div>
        </details>
      ) : null}
    </SettingsSection>
  )
}

function OpenPRCount({
  cohort,
  maturityDays,
}: {
  cohort: Pick<PRMergeRateCohort, "mature_pending" | "waiting">
  maturityDays: number
}) {
  const [open, setOpen] = useState(false)

  if (cohort.waiting === 0 && cohort.mature_pending === 0) {
    return <span>0</span>
  }

  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        closeOnClick={false}
        onClick={() => setOpen(true)}
        className="cursor-help rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        {cohort.waiting + cohort.mature_pending}
      </TooltipTrigger>
      <TooltipPopup>
        <div>
          {cohort.waiting} open for less than {maturityDays} days
        </div>
        <div>
          {cohort.mature_pending} open for {maturityDays} days or longer
        </div>
      </TooltipPopup>
    </Tooltip>
  )
}

function AvgTimeToMerge({ cohort }: { cohort: PRMergeRateCohort }) {
  return (
    <span>
      {cohort.avg_merge_seconds == null
        ? "—"
        : formatAvgDuration(cohort.avg_merge_seconds)}
    </span>
  )
}

function AvgTimeToPR({ cohort }: { cohort: PRMergeRateCohort }) {
  if (cohort.avg_delivery_seconds == null) {
    return <span>—</span>
  }
  return (
    <Tooltip>
      <TooltipTrigger className="cursor-help rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {formatAvgDuration(cohort.avg_delivery_seconds)}
      </TooltipTrigger>
      <TooltipPopup className="max-w-xs">
        Based on PRs whose opening run has a valid start time, regardless of
        outcome; PRs with missing or invalid timing are excluded.
      </TooltipPopup>
    </Tooltip>
  )
}

const PR_OUTCOME_COLUMNS: Array<SortableColumn<PROutcomesSort>> = [
  {
    key: "model",
    label: "Opening model",
    align: "left",
    defaultDirection: "asc",
  },
  { key: "prs_opened", label: "PRs opened", align: "right" },
  { key: "merged", label: "Merged", align: "right" },
  {
    key: "closed_without_merge",
    label: "Closed without merge",
    align: "right",
  },
  { key: "open", label: "Open", align: "right" },
  {
    key: "median_distance",
    label: "Median distance",
    align: "right",
    tooltip:
      "Median post-open line edit distance across merged PRs. Higher means the final diff changed more after the PR opened.",
  },
  {
    key: "merge_rate",
    label: "Merge rate",
    align: "right",
  },
  { key: "avg_delivery_seconds", label: "Avg time to PR", align: "right" },
  {
    key: "avg_merge_seconds",
    label: "Avg time to merge",
    align: "right",
    tooltip: "Unmerged PRs are excluded.",
  },
]

function prOutcomeSortValue(cohort: PRMergeRateCohort, sort: PROutcomesSort) {
  switch (sort) {
    case "model":
      return safeModelLabel(cohort.model_id ?? "") || "Unavailable"
    case "prs_opened":
      return cohort.cohort_size
    case "merged":
      return cohort.merged
    case "closed_without_merge":
      return cohort.closed_without_merge
    case "open":
      return cohort.waiting + cohort.mature_pending
    case "median_distance":
      return cohort.median_distance_basis_points ?? null
    case "merge_rate":
      return cohort.mature_cohort_merge_share
    case "avg_delivery_seconds":
      return cohort.avg_delivery_seconds
    case "avg_merge_seconds":
      return cohort.avg_merge_seconds
  }
}

function PRMergeRateTable({
  cohorts,
  maturityDays,
}: {
  cohorts: PRMergeRateCohort[]
  maturityDays: number
}) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [sort, setSort] = useState<PROutcomesSort>("prs_opened")
  const [direction, setDirection] = useState<SortDirection>("desc")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const sortedCohorts = [...cohorts].sort((a, b) => {
    const aValue = prOutcomeSortValue(a, sort)
    const bValue = prOutcomeSortValue(b, sort)
    if (aValue == null) return bValue == null ? 0 : 1
    if (bValue == null) return -1
    const comparison =
      typeof aValue === "string" && typeof bValue === "string"
        ? aValue.localeCompare(bValue, undefined, { sensitivity: "base" })
        : Number(aValue) - Number(bValue)
    return direction === "asc" ? comparison : -comparison
  })
  const pageCount = Math.max(1, Math.ceil(sortedCohorts.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const rows = sortedCohorts.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              {PR_OUTCOME_COLUMNS.map((column, index, columns) => (
                <SortableHeader
                  key={column.key}
                  column={
                    column.key === "merge_rate"
                      ? {
                          ...column,
                          tooltip: `Includes merged and closed PRs, plus PRs open for at least ${maturityDays} days. Newer open PRs are excluded.`,
                        }
                      : column
                  }
                  sortKey={sort}
                  sortDirection={direction}
                  onSort={(nextSort, defaultDirection) => {
                    setDirection(
                      sort === nextSort
                        ? direction === "asc"
                          ? "desc"
                          : "asc"
                        : defaultDirection
                    )
                    setSort(nextSort)
                    setPage(1)
                  }}
                  className={`${index === 0 ? "pr-0 pl-4" : index === columns.length - 1 ? "pr-4 pl-0" : "px-0"} ${column.align === "right" ? "text-right" : "text-left"}`}
                />
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((cohort) => {
              const key = `${cohort.model_id}-${cohort.model_attribution_quality}`
              const hasMultipleEfforts = cohort.efforts.length > 1
              const isExpanded = hasMultipleEfforts && expanded.has(key)
              const modelLabel = cohort.model_id
                ? safeModelLabel(cohort.model_id) || "Unavailable"
                : "Unavailable"
              return (
                <Fragment key={key}>
                  <tr>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {hasMultipleEfforts ? (
                          <button
                            type="button"
                            className="-ml-1 size-5.5 shrink-0 rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                            aria-expanded={isExpanded}
                            aria-label={`${isExpanded ? "Collapse" : "Expand"} ${modelLabel} reasoning efforts`}
                            onClick={() =>
                              setExpanded((current) => {
                                const next = new Set(current)
                                if (next.has(key)) next.delete(key)
                                else next.add(key)
                                return next
                              })
                            }
                          >
                            {isExpanded ? (
                              <ChevronDown className="size-3.5" />
                            ) : (
                              <ChevronRight className="size-3.5" />
                            )}
                          </button>
                        ) : (
                          <span
                            aria-hidden="true"
                            className="-ml-1 size-5.5 shrink-0"
                          />
                        )}
                        <div>
                          <div className="font-medium">{modelLabel}</div>
                          <div className="text-muted-foreground">
                            {hasMultipleEfforts
                              ? `All efforts · ${cohort.model_attribution_quality} attribution`
                              : `${formatEffort(cohort.efforts[0]?.effort)} · ${cohort.model_attribution_quality} attribution`}
                          </div>
                        </div>
                      </div>
                    </td>
                    <PRMergeRateCells
                      cohort={cohort}
                      maturityDays={maturityDays}
                    />
                    <td className="px-4 py-3 text-right tabular-nums">
                      <AvgTimeToPR cohort={cohort} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <AvgTimeToMerge cohort={cohort} />
                    </td>
                  </tr>
                  {isExpanded
                    ? cohort.efforts.map((effort) => (
                        <tr
                          key={`${key}-${effort.effort ?? "unknown"}`}
                          className="bg-muted/35"
                        >
                          <td className="py-3 pr-2 pl-11 font-medium">
                            {formatEffort(effort.effort)}
                          </td>
                          <PRMergeRateCells
                            cohort={effort}
                            maturityDays={maturityDays}
                          />
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span title="Average shown at the model level">
                              —
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span title="Average shown at the model level">
                              —
                            </span>
                          </td>
                        </tr>
                      ))
                    : null}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={currentPage}
        pageSize={pageSize}
        total={cohorts.length}
        onPageChange={setPage}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
        }}
      />
    </div>
  )
}

function usageColumns(
  scope: UsageScope,
  period: UsageLeaderboardPeriod
): Array<SortableColumn<UsageLeaderboardSort>> {
  return [
    { key: "rank", label: "Rank", align: "left", defaultDirection: "asc" },
    { key: "user", label: "User", align: "left", defaultDirection: "asc" },
    {
      key: "favorite_model",
      label: "Favorite Model",
      align: "left",
      defaultDirection: "asc",
    },
    {
      key: scope === "threads" ? "threads" : "invocations",
      label: scope === "threads" ? "Threads" : "Invocations",
      align: "right",
    },
    {
      key: "avg_invocations_per_thread",
      label: "Avg Invocations / Thread",
      align: "right",
    },
    { key: "total_tokens", label: "Tokens", align: "right" },
    { key: "total_cost_usd", label: "Cost", align: "right" },
    {
      key:
        scope === "threads" ? "avg_thread_seconds" : "avg_invocation_seconds",
      label:
        scope === "threads" ? "Avg Thread Duration" : "Avg Invocation Duration",
      align: "right",
    },
    { key: "prs_opened", label: "PRs Opened", align: "right" },
    { key: "merged_prs", label: "Merged PRs", align: "right" },
    {
      key: "merged_prs_per_thread",
      label: "Merged PRs / Thread",
      align: "right",
      tooltip: `Merged PRs ÷ distinct threads ${
        period === "all"
          ? "all time"
          : `in the ${PERIOD_LABELS[period].toLowerCase()}`
      } — an aggregate ratio, not a per-thread outcome. One thread can open several PRs and many threads open none, so 1.00 does not mean every thread merged a PR.`,
    },
    { key: "agent_loc", label: "Agent LOC", align: "right" },
  ]
}

function SortableHeader<Key extends string>({
  column,
  sortKey,
  sortDirection,
  onSort,
  className,
}: {
  column: SortableColumn<Key>
  sortKey: Key
  sortDirection: SortDirection
  onSort: (key: Key, direction: SortDirection) => void
  className: string
}) {
  const isActive = sortKey === column.key
  const Icon = isActive
    ? sortDirection === "asc"
      ? ArrowUpNarrowWide
      : ArrowDownNarrowWide
    : ArrowUpDown
  const ariaSort = isActive
    ? sortDirection === "asc"
      ? "ascending"
      : "descending"
    : undefined

  const button = (
    <button
      type="button"
      onClick={() => onSort(column.key, column.defaultDirection ?? "desc")}
      className={`flex w-full items-center gap-1 rounded-sm px-2 py-3 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
        column.align === "right" ? "justify-end" : "justify-start"
      } ${isActive ? "text-foreground" : ""} ${
        column.tooltip
          ? "cursor-help underline decoration-dotted underline-offset-2"
          : ""
      }`}
    >
      {column.label}
      <Icon
        className={`size-3 shrink-0 ${isActive ? "" : "text-muted-foreground/50"}`}
        aria-hidden
      />
    </button>
  )

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`${className} p-0 font-normal`}
    >
      {column.tooltip ? (
        <Tooltip>
          <TooltipTrigger render={button} />
          <TooltipPopup className="max-w-xs">{column.tooltip}</TooltipPopup>
        </Tooltip>
      ) : (
        button
      )}
    </th>
  )
}

function formatEffort(effort: string | null | undefined) {
  return effort
    ? effort.charAt(0).toUpperCase() + effort.slice(1)
    : "Unknown / legacy"
}

function PRMergeRateCells({
  cohort,
  maturityDays,
}: {
  cohort: Pick<
    PRMergeRateCohort,
    | "cohort_size"
    | "merged"
    | "closed_without_merge"
    | "mature_pending"
    | "waiting"
    | "mature_cohort_merge_share"
    | "median_distance_basis_points"
    | "distance_sample_size"
  >
  maturityDays: number
}) {
  return (
    <>
      <td className="px-2 py-3 text-right tabular-nums">
        {cohort.cohort_size}
      </td>
      <td className="px-2 py-3 text-right tabular-nums">{cohort.merged}</td>
      <td className="px-2 py-3 text-right tabular-nums">
        {cohort.closed_without_merge}
      </td>
      <td className="px-2 py-3 text-right tabular-nums">
        <OpenPRCount cohort={cohort} maturityDays={maturityDays} />
      </td>
      <td className="px-2 py-3 text-right tabular-nums">
        <Tooltip>
          <TooltipTrigger className="cursor-help rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {cohort.median_distance_basis_points == null
              ? "—"
              : `${(cohort.median_distance_basis_points / 100).toFixed(1)}%`}
          </TooltipTrigger>
          <TooltipPopup>
            {cohort.distance_sample_size ?? 0} merged PR
            {cohort.distance_sample_size === 1 ? "" : "s"} measured
          </TooltipPopup>
        </Tooltip>
      </td>
      <td className="px-4 py-3 text-right text-sm font-semibold tabular-nums">
        {cohort.mature_cohort_merge_share == null
          ? "—"
          : formatPercent(cohort.mature_cohort_merge_share)}
      </td>
    </>
  )
}

function UsageTable({
  scope,
  period,
  currentUserRank,
  rows,
  totalMembers,
  page,
  pageSize,
  sort,
  direction,
  isUpdating,
  onSort,
  onPageChange,
  onPageSizeChange,
}: {
  scope: UsageScope
  period: UsageLeaderboardPeriod
  currentUserRank: number | null
  rows: Array<UsageLeaderboardRow>
  totalMembers: number
  page: number
  pageSize: number
  sort: UsageLeaderboardSort
  direction: SortDirection
  isUpdating: boolean
  onSort: (key: UsageLeaderboardSort, direction: SortDirection) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  return (
    <div>
      <div className="overflow-x-auto">
        <table aria-busy={isUpdating} className="w-full min-w-[1040px] text-xs">
          {isUpdating ? (
            <caption className="sr-only">Updating leaderboard</caption>
          ) : null}
          <thead className="border-b border-border text-xs text-muted-foreground">
            <tr>
              {usageColumns(scope, period).map((column, index, columns) => (
                <SortableHeader
                  key={column.key}
                  column={column}
                  sortKey={sort}
                  sortDirection={direction}
                  onSort={onSort}
                  className={`${index === 0 ? "w-14 pr-0 pl-4" : index === columns.length - 1 ? "pr-4 pl-0" : "px-0"} ${column.align === "right" ? "text-right" : "text-left"}`}
                />
              ))}
            </tr>
          </thead>
          <tbody
            className={`divide-y divide-border ${isUpdating ? "opacity-50" : ""}`}
          >
            {rows.map((row) => (
              <tr
                key={`${row.rank}-${row.user.github_login ?? row.user.email ?? row.user.name}`}
              >
                <td className="px-4 py-3 text-muted-foreground">{row.rank}</td>
                <td className="px-2 py-3">
                  <UserCell
                    row={row}
                    isCurrentUser={row.rank === currentUserRank}
                  />
                </td>
                <td className="max-w-48 px-2 py-3 text-muted-foreground">
                  <div className="truncate">
                    {safeModelLabel(row.favorite_model) || "Unavailable"}
                  </div>
                  <div className="capitalize">
                    {row.favorite_model_effort === undefined
                      ? null
                      : (row.favorite_model_effort ?? "Unknown")}
                  </div>
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {formatNumber(
                    scope === "threads" ? (row.threads ?? 0) : row.invocations
                  )}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {row.threads
                    ? (row.invocations / row.threads).toFixed(1)
                    : "—"}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {formatNumber(row.total_tokens)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  <UsageCost row={row} />
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {formatDuration(
                    scope === "threads"
                      ? (row.avg_thread_seconds ?? 0)
                      : row.avg_invocation_seconds
                  )}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {formatNumber(row.prs_opened)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {formatNumber(row.merged_prs)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums">
                  {(row.merged_prs_per_thread ?? 0).toFixed(2)}
                </td>
                <td
                  className="px-4 py-3 text-right tabular-nums"
                  title={`${formatNumber(row.additions)} additions, ${formatNumber(row.deletions)} deletions`}
                >
                  {formatNumber(row.agent_loc)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={totalMembers}
        disabled={isUpdating}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </div>
  )
}

function TablePagination({
  page,
  pageSize,
  total,
  disabled = false,
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: number
  total: number
  disabled?: boolean
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  if (total <= 10) return null

  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const start = total ? (page - 1) * pageSize + 1 : 0
  const end = Math.min(page * pageSize, total)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-xs text-muted-foreground">
      <span>
        {formatNumber(start)}–{formatNumber(end)} of {formatNumber(total)}
      </span>
      <div className="flex items-center gap-2">
        <span>Rows per page</span>
        <Select
          disabled={disabled}
          value={String(pageSize)}
          onValueChange={(value) => onPageSizeChange(Number(value))}
        >
          <SelectTrigger aria-label="Rows per page" className="w-20">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || page === 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        <span>
          Page {page} of {pageCount}
        </span>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}

function ReviewerStats({ stats }: { stats: ReviewerStatsPayload }) {
  const cards = [
    {
      label: "Reviewed PRs",
      value: stats.reviewed_prs,
      helper: `${formatNumber(stats.prs_with_findings)} with findings`,
    },
    {
      label: "Issues surfaced",
      value: stats.surfaced_findings,
      helper: `${formatNumber(stats.findings_recorded)} recorded`,
    },
    {
      label: "Addressed & resolved",
      value: stats.addressed_findings,
      helper: `${formatPercent(stats.resolution_rate)} of surfaced`,
    },
    {
      label: "Resolved after update",
      value: stats.resolved_after_update,
      helper: "Resolved on a later PR head",
    },
    {
      label: "Awaiting follow-up",
      value: stats.unresolved_surfaced_findings,
      helper: "Surfaced but not resolved/dismissed",
    },
    {
      label: "Dismissed",
      value: stats.dismissed_findings,
      helper: `${formatNumber(stats.human_replies)} human replies tracked`,
    },
  ]

  return (
    <div className="space-y-4 p-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <div key={card.label} className="rounded-md border border-border p-3">
            <div className="text-xs text-muted-foreground">{card.label}</div>
            <div className="mt-1 text-lg font-medium tabular-nums">
              {formatNumber(card.value)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {card.helper}
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
        <CounterList title="Top categories" rows={stats.top_categories} />
        <CounterList title="Severity mix" rows={severityRows(stats)} />
      </div>
    </div>
  )
}

function severityRows(
  stats: ReviewerStatsPayload
): Array<{ name: string; count: number }> {
  return ["critical", "high", "medium", "low"]
    .map((severity) => ({
      name: severity,
      count: stats.severity_counts[severity] ?? 0,
    }))
    .filter((row) => row.count > 0)
}

function CounterList({
  title,
  rows,
}: {
  title: string
  rows: Array<{ name: string; count: number }>
}) {
  return (
    <div>
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {rows.length ? (
        <ul className="mt-2 space-y-2 text-xs">
          {rows.map((row) => (
            <li
              key={row.name}
              className="flex items-center justify-between gap-3"
            >
              <span className="truncate text-foreground">{row.name}</span>
              <span className="text-muted-foreground tabular-nums">
                {formatNumber(row.count)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">No data yet.</p>
      )}
    </div>
  )
}

function UserCell({
  row,
  isCurrentUser,
}: {
  row: UsageLeaderboardRow
  isCurrentUser: boolean
}) {
  const initials = initialsFor(row.user.name)
  const detail = row.user.github_login
  const profileUrl = githubProfileUrl(row.user.github_login)
  const name = profileUrl ? (
    <a
      href={profileUrl}
      target="_blank"
      rel="noreferrer"
      className="truncate font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      {row.user.name}
    </a>
  ) : (
    <span className="truncate font-medium text-foreground">
      {row.user.name}
    </span>
  )
  const avatar = (
    <Avatar>
      {row.user.avatar_url && (
        <AvatarImage src={row.user.avatar_url} alt={row.user.name} />
      )}
      <AvatarFallback>{initials}</AvatarFallback>
    </Avatar>
  )
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {profileUrl ? (
        <a
          href={profileUrl}
          target="_blank"
          rel="noreferrer"
          aria-hidden="true"
          tabIndex={-1}
        >
          {avatar}
        </a>
      ) : (
        avatar
      )}
      <div className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-1.5">
          {name}
          {isCurrentUser ? (
            <Badge variant="secondary" aria-label="You">
              You
            </Badge>
          ) : null}
        </div>
        {detail && detail !== row.user.name ? (
          <span className="truncate text-xs text-muted-foreground">
            {detail}
          </span>
        ) : null}
      </div>
    </div>
  )
}

function githubProfileUrl(login: string | null): string | null {
  if (!login) return null
  return `https://github.com/${encodeURIComponent(login)}`
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return "?"
  const first = parts[0] ?? "?"
  const second = parts[1]
  if (!second) return first.slice(0, 2).toUpperCase()
  return `${first[0] ?? ""}${second[0] ?? ""}`.toUpperCase()
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value)
}

function UsageCost({ row }: { row: UsageLeaderboardRow }) {
  if (row.invocations === 0) return <span>—</span>

  const missing = row.invocations_without_cost
  const partial = row.invocations_with_partial_cost
  const coverageKnown = missing != null && partial != null
  if (coverageKnown && missing === 0 && partial === 0) {
    return <span>{formatCurrency(row.total_cost_usd)}</span>
  }

  const unavailable = coverageKnown
    ? missing >= row.invocations
    : row.total_cost_usd === 0
  const label = unavailable ? "Unavailable" : "Incomplete"
  const explanation = coverageKnown
    ? [
        unavailable ? "No costs have been recorded." : "Recorded cost so far.",
        missing > 0
          ? `Costs are missing for ${missing} of ${row.invocations} invocations (${formatPercent(missing / row.invocations)}).`
          : "",
        partial > 0
          ? `Costs are partial for ${partial} of ${row.invocations} invocations.`
          : "",
      ]
        .filter(Boolean)
        .join(" ")
    : "Cost coverage is unavailable for these invocations."

  return (
    <div className="flex flex-col items-end gap-0.5">
      <span>{unavailable ? "—" : formatCurrency(row.total_cost_usd)}</span>
      <Tooltip>
        <TooltipTrigger
          aria-label={`Cost ${label.toLowerCase()}`}
          className="cursor-help rounded-sm text-[10px] text-muted-foreground underline decoration-dotted underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          {label}
        </TooltipTrigger>
        <TooltipPopup className="max-w-xs">{explanation}</TooltipPopup>
      </Tooltip>
    </div>
  )
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

function formatDuration(value: number): string {
  if (value < 60) return `${Math.round(value)}s`
  return `${Math.round(value / 60)}m`
}

function formatAvgDuration(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}

function formatPercent(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
    style: "percent",
  }).format(value)
}
