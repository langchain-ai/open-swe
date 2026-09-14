import { createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowDownNarrowWide,
  ArrowUpNarrowWide,
  ArrowUpDown,
} from "lucide-react"
import { useMemo, useState } from "react"

import type {
  AnalyticsMetadata,
  PRMergeRateCohort,
  ReviewerStatsPayload,
  UsageLeaderboardPeriod,
  UsageLeaderboardRow,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
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
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/usage")({
  validateSearch: (search: Record<string, unknown>) => ({
    period: typeof search.period === "string" ? search.period : undefined,
  }),
  component: UsagePage,
})

const PAGE_SIZES = [10, 25, 50, 100] as const

type SortDirection = "asc" | "desc"

interface SortableColumn {
  key: string
  label: string
  align: "left" | "right"
  sortValue: (row: UsageLeaderboardRow) => number | string
}

const PERIOD_LABELS: Record<UsageLeaderboardPeriod, string> = {
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
}

function UsagePage() {
  const session = useSession()
  const period =
    (Route.useSearch().period as UsageLeaderboardPeriod | undefined) ?? "30d"
  const navigate = Route.useNavigate()
  const activePeriod: UsageLeaderboardPeriod = ["7d", "30d", "all"].includes(
    period
  )
    ? period
    : "30d"

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell user={session.data} title="Usage" className="max-w-5xl">
      <UsageAnalytics
        period={activePeriod}
        login={session.data.login}
        isAdmin={session.data.is_admin}
        onPeriodChange={(value) =>
          navigate({ to: "/usage", search: { period: value } })
        }
      />
    </AppShell>
  )
}

export function UsageAnalytics({
  period: activePeriod,
  login,
  isAdmin,
  onPeriodChange,
}: {
  period: UsageLeaderboardPeriod
  login: string
  isAdmin: boolean
  onPeriodChange: (period: UsageLeaderboardPeriod) => void
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
      onPeriodChange={onPeriodChange}
    />
  )
}

function UsageAnalyticsPeriod({
  period: activePeriod,
  login,
  isAdmin,
  pageSize: leaderboardPageSize,
  onPageSizeChange: setLeaderboardPageSize,
  onPeriodChange,
}: {
  period: UsageLeaderboardPeriod
  login: string
  isAdmin: boolean
  pageSize: number
  onPageSizeChange: (pageSize: number) => void
  onPeriodChange: (period: UsageLeaderboardPeriod) => void
}) {
  const [leaderboardPage, setLeaderboardPage] = useState(1)
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
    ],
    queryFn: () =>
      api.usageLeaderboard(
        activePeriod,
        leaderboardPageSize,
        leaderboardCursors[leaderboardPage - 1]
      ),
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 503) && count < 2,
  })
  const report = usePRMergeRateReport(activePeriod, login, isAdmin)
  const metadata = [
    leaderboard.isError ? undefined : leaderboard.data,
    report.isError ? undefined : report.data,
  ].filter((data): data is NonNullable<typeof data> => data != null)

  return (
    <>
      <AnalyticsCoverage reports={metadata} />
      <PRMergeRateSection report={report} />

      <SettingsSection
        title="Agent leaderboard"
        description="Ranked by merged PRs, then agent lines of code, PRs opened, and invocations."
        action={
          <Select
            value={activePeriod}
            onValueChange={(value) =>
              onPeriodChange(value as UsageLeaderboardPeriod)
            }
          >
            <SelectTrigger className="w-36">
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
            rows={leaderboard.data.rows}
            totalMembers={leaderboard.data.total_members}
            page={leaderboardPage}
            pageSize={leaderboardPageSize}
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
      {!leaderboard.isError && leaderboard.data?.generated_at_ms ? (
        <p className="text-right text-xs text-muted-foreground">
          Updated {formatTime(leaderboard.data.generated_at_ms)}
        </p>
      ) : null}
    </>
  )
}

function AnalyticsCoverage({ reports }: { reports: AnalyticsMetadata[] }) {
  if (!reports.length) return null
  const latest = reports.reduce((a, b) => (a.as_of > b.as_of ? a : b))
  return (
    <div
      className="space-y-1 text-xs text-muted-foreground"
      role="status"
      aria-label="Analytics coverage"
    >
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
        {reports.some((data) => data.has_pending_events)
          ? "Some captured events are still waiting to be processed. "
          : ""}
        {reports.some((data) => data.has_failed_events)
          ? "Some events could not be processed. Reports may be incomplete. "
          : ""}
        Reports checked {new Date(latest.as_of).toLocaleString()}.
      </p>
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
      !(error instanceof ApiError && error.status === 503) && count < 2,
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
        <PRMergeRateTable key={data.period} cohorts={data.cohorts} />
      ) : (
        <p
          className="p-6 text-center text-xs text-muted-foreground"
          role="status"
        >
          {emptyMessage}
        </p>
      )}
      {data ? (
        <details className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
            How these numbers work
          </summary>
          <div className="mt-3 space-y-3">
            <ul className="list-disc space-y-1 pl-4">
              <li>
                <strong>Waiting:</strong> still open and less than{" "}
                {data.maturity_days} days old. These PRs are excluded from both
                rates.
              </li>
              <li>
                <strong>Mature pending:</strong> still open and at least{" "}
                {data.maturity_days} days old. They have no final outcome yet.
              </li>
              <li>
                <strong>Decided rate:</strong> the percentage of merged or
                closed PRs that were merged. Open PRs are excluded.
              </li>
              <li>
                <strong>Mature share:</strong> the percentage merged among
                merged, closed, and mature pending PRs.
              </li>
            </ul>
            <p>
              For example, 3 merged PRs, 1 closed without merging, and 1 mature
              pending PR give a decided rate of 75% (3 of 4) and a mature share
              of 60% (3 of 5). Waiting PRs do not change either rate.
            </p>
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

function PRMergeRateTable({ cohorts }: { cohorts: PRMergeRateCohort[] }) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const pageCount = Math.max(1, Math.ceil(cohorts.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const rows = cohorts.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-xs">
        <thead className="border-b border-border text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-normal">
              Opening configured model
            </th>
            <th className="px-2 py-3 text-right font-normal">Merged</th>
            <th className="px-2 py-3 text-right font-normal">Closed</th>
            <th className="px-2 py-3 text-right font-normal">Mature pending</th>
            <th className="px-2 py-3 text-right font-normal">Waiting</th>
            <th className="px-2 py-3 text-right font-normal">Decided rate</th>
            <th className="px-4 py-3 text-right font-normal">Mature share</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((cohort) => (
            <tr key={`${cohort.model_id}-${cohort.model_attribution_quality}`}>
              <td className="px-4 py-3">
                <div className="font-medium">
                  {cohort.model_id ?? "Unavailable"}
                </div>
                <div className="text-muted-foreground">
                  {cohort.model_attribution_quality} attribution
                </div>
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {cohort.merged}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {cohort.closed_without_merge}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {cohort.mature_pending}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {cohort.waiting}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {cohort.decided_merge_rate == null
                  ? "—"
                  : formatPercent(cohort.decided_merge_rate)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                {cohort.mature_cohort_merge_share == null
                  ? "—"
                  : formatPercent(cohort.mature_cohort_merge_share)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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

const USAGE_COLUMNS: Array<SortableColumn> = [
  {
    key: "rank",
    label: "Rank",
    align: "left",
    sortValue: (row) => row.rank,
  },
  {
    key: "user",
    label: "User",
    align: "left",
    sortValue: (row) => row.user.name.toLowerCase(),
  },
  {
    key: "favorite_model",
    label: "Favorite Model",
    align: "left",
    sortValue: (row) => row.favorite_model.toLowerCase(),
  },
  {
    key: "invocations",
    label: "Invocations",
    align: "right",
    sortValue: (row) => row.invocations,
  },
  {
    key: "total_tokens",
    label: "Tokens",
    align: "right",
    sortValue: (row) => row.total_tokens,
  },
  {
    key: "total_cost_usd",
    label: "Cost",
    align: "right",
    sortValue: (row) => row.total_cost_usd,
  },
  {
    key: "avg_invocation_seconds",
    label: "Avg Invocation Duration",
    align: "right",
    sortValue: (row) => row.avg_invocation_seconds,
  },
  {
    key: "prs_opened",
    label: "PRs Opened",
    align: "right",
    sortValue: (row) => row.prs_opened,
  },
  {
    key: "merged_prs",
    label: "Merged PRs",
    align: "right",
    sortValue: (row) => row.merged_prs,
  },
  {
    key: "agent_loc",
    label: "Agent LOC",
    align: "right",
    sortValue: (row) => row.agent_loc,
  },
]

function compareSortValues(a: number | string, b: number | string): number {
  if (typeof a === "number" && typeof b === "number") return a - b
  return String(a).localeCompare(String(b))
}

function SortableHeader({
  column,
  sortKey,
  sortDirection,
  onSort,
  className,
}: {
  column: SortableColumn
  sortKey: string | null
  sortDirection: SortDirection
  onSort: (key: string) => void
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

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`${className} p-0 font-normal`}
    >
      <button
        type="button"
        onClick={() => onSort(column.key)}
        className={`flex w-full items-center gap-1 rounded-sm px-2 py-3 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
          column.align === "right" ? "justify-end" : "justify-start"
        } ${isActive ? "text-foreground" : ""}`}
      >
        {column.label}
        <Icon
          className={`size-3 shrink-0 ${isActive ? "" : "text-muted-foreground/50"}`}
          aria-hidden
        />
      </button>
    </th>
  )
}

function UsageTable({
  rows,
  totalMembers,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  rows: Array<UsageLeaderboardRow>
  totalMembers: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc")

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc")
    } else {
      setSortKey(key)
      setSortDirection("desc")
    }
  }

  const sortedRows = useMemo(() => {
    const column = USAGE_COLUMNS.find((col) => col.key === sortKey)
    if (!column) return rows
    const sorted = [...rows].sort((a, b) =>
      compareSortValues(column.sortValue(a), column.sortValue(b))
    )
    return sortDirection === "desc" ? sorted.reverse() : sorted
  }, [rows, sortKey, sortDirection])

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1040px] text-xs">
        <thead className="border-b border-border text-xs text-muted-foreground">
          <tr>
            {USAGE_COLUMNS.map((column, index) => (
              <SortableHeader
                key={column.key}
                column={column}
                sortKey={sortKey}
                sortDirection={sortDirection}
                onSort={handleSort}
                className={`${index === 0 ? "w-14 pr-0 pl-4" : index === USAGE_COLUMNS.length - 1 ? "pr-4 pl-0" : "px-0"} ${column.align === "right" ? "text-right" : "text-left"}`}
              />
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {sortedRows.map((row) => (
            <tr
              key={`${row.rank}-${row.user.github_login ?? row.user.email ?? row.user.name}`}
            >
              <td className="px-4 py-3 text-muted-foreground">{row.rank}</td>
              <td className="px-2 py-3">
                <UserCell row={row} />
              </td>
              <td className="max-w-48 truncate px-2 py-3 text-muted-foreground">
                {row.favorite_model}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.invocations)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.total_tokens)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                <UsageCost row={row} />
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatDuration(row.avg_invocation_seconds)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.prs_opened)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.merged_prs)}
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
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={totalMembers}
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
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
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
          disabled={page === 1}
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
          disabled={page >= pageCount}
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

function UserCell({ row }: { row: UsageLeaderboardRow }) {
  const initials = initialsFor(row.user.name)
  const detail = row.user.email ?? row.user.github_login ?? "unknown"
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Avatar>
        {row.user.avatar_url && (
          <AvatarImage src={row.user.avatar_url} alt={row.user.name} />
        )}
        <AvatarFallback>{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-foreground">
          {row.user.name}
        </span>
        {detail !== row.user.name ? (
          <span className="truncate text-xs text-muted-foreground">
            {detail}
          </span>
        ) : null}
      </div>
    </div>
  )
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return "?"
  const first = parts[0] ?? "?"
  const second = parts[1]
  if (!second) return first.slice(0, 2).toUpperCase()
  return `${first[0] ?? ""}${second[0] ?? ""}`.toUpperCase()
}

function formatTime(value: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(value)
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

function formatPercent(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
    style: "percent",
  }).format(value)
}
