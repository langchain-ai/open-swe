import { createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { Fragment, useState } from "react"
import type { ReactNode } from "react"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  MetricCard,
  MetricCardGrid,
} from "@langchain/gtm-platform-design-system/patterns/metric-card"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StatReadout } from "@langchain/gtm-platform-design-system/patterns/stat-readout"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@langchain/gtm-platform-design-system/ui/table"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"

import type {
  AnalyticsMetadata,
  PRMergeRateCohort,
  PRMergeRateEffort,
  PRMergeRatePayload,
  PRMergeRateResponse,
  ReviewerStatsPayload,
  SortDirection,
  UsageLeaderboardPeriod,
  UsageLeaderboardRow,
  UsageLeaderboardSort,
} from "@/lib/api"
import { SettingsPage } from "@/components/AppShell"
import { CopyDiagnosticsButton } from "@/components/CopyDiagnosticsButton"
import { TablePagination } from "@/components/TablePagination"
import {
  AlertCircle,
  AlertTriangle,
  ArrowUpDown,
  BarChart,
  Bug,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  GitPullRequest,
  MinusCircle,
  RefreshCw,
  SortAsc,
  SortDesc,
  Users,
  type Glyph,
} from "@/components/glyphs"
import { api, ApiError } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { safeModelLabel } from "@/lib/modelLabel"
import {
  buildUsageDiagnostics,
  metricAvailability,
  type MetricAvailability,
} from "@/lib/usage-diagnostics"

export const Route = createFileRoute("/usage")({
  validateSearch: (search: Record<string, unknown>) => ({
    period: typeof search.period === "string" ? search.period : undefined,
  }),
  head: () => ({ meta: [{ title: pageTitle("Usage") }] }),
  component: UsagePage,
})

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
  | "mean_distance"
  | "merge_rate"
  | "avg_delivery_seconds"
  | "avg_merge_seconds"
const PERIOD_LABELS: Record<UsageLeaderboardPeriod, string> = {
  "24h": "Last 24h",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
}
const PERIODS = [
  "24h",
  "7d",
  "30d",
  "all",
] as const satisfies readonly UsageLeaderboardPeriod[]
const USAGE_SCOPES = [
  "invocations",
  "threads",
] as const satisfies readonly UsageScope[]

/* A number column: right-aligned, mono, tabular, so digits stack under a poll. */
const NUMBER_CELL_CLASS = "py-2 text-right font-mono tabular-nums"
/* A value with an explanation behind it: dotted underline, help cursor, focusable. */
const HINT_TRIGGER_CLASS =
  "cursor-help rounded-tick underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-primary"
/* A name column that stays put while a wide table scrolls sideways. */
const PINNED_CELL_CLASS = "sticky left-0 z-10 bg-panel"

function isPeriod(value: string): value is UsageLeaderboardPeriod {
  return (PERIODS as readonly string[]).includes(value)
}

function isUsageScope(value: string | undefined): value is UsageScope {
  return value === "invocations" || value === "threads"
}

function UsagePage() {
  const requested = Route.useSearch().period
  const navigate = Route.useNavigate()
  const activePeriod: UsageLeaderboardPeriod =
    requested !== undefined && isPeriod(requested) ? requested : "7d"

  return (
    <SettingsPage
      title="Usage"
      contentWidth="wide"
      action={
        <UsageDateRange
          period={activePeriod}
          onPeriodChange={(value) =>
            void navigate({ to: "/usage", search: { period: value } })
          }
        />
      }
    >
      {(user) => (
        <UsageAnalytics
          period={activePeriod}
          login={user.login}
          isAdmin={user.is_admin}
        />
      )}
    </SettingsPage>
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
    <ToggleGroup
      aria-label="Date range"
      value={[activePeriod]}
      onValueChange={(values) => {
        const next = values[0]
        if (next !== undefined && isPeriod(next)) onPeriodChange(next)
      }}
    >
      {PERIODS.map((value) => (
        <ToggleGroupItem key={value} value={value}>
          {PERIOD_LABELS[value]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
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

function LoadingRows({ count, label }: { count: number; label?: string }) {
  return (
    <Stack
      gap="sm"
      padding="lg"
      role={label === undefined ? undefined : "status"}
      aria-label={label}
    >
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </Stack>
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
  const [usageScope, setUsageScope] = useState<UsageScope>("threads")
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
  // A refresh that fails keeps the last successful report visible, but the
  // failure stays announced in the coverage details until one succeeds —
  // manual or automatic (the query also refetches on interval/focus).
  const [reportError, setReportError] = useState<ApiError | null>(null)
  const report = usePRMergeRateReport(
    activePeriod,
    login,
    isAdmin,
    setReportError
  )
  const refreshing = leaderboard.isFetching || report.isFetching
  const refreshNow = () => {
    void leaderboard.refetch()
    // refetch() resolves on failure too, so the error has to come off the result.
    void report.refetch().then((result) => {
      const error = result.error
      setReportError(
        error == null
          ? null
          : error instanceof ApiError
            ? error
            : new ApiError(0, "unknown")
      )
    })
  }
  const metadata = [
    leaderboard.isError ? undefined : leaderboard.data,
    report.isError ? undefined : report.data?.payload,
  ].filter((data): data is NonNullable<typeof data> => data != null)
  const leaderboardFailed = !leaderboard.isLoading && leaderboard.isError
  const periodLabel = PERIOD_LABELS[activePeriod].toLowerCase()

  const changeScope = (scope: UsageScope) => {
    setUsageScope(scope)
    if (sort === "invocations" || sort === "threads") {
      setSort(scope)
    } else if (
      sort === "avg_invocation_seconds" ||
      sort === "avg_thread_seconds"
    ) {
      setSort(
        scope === "threads" ? "avg_thread_seconds" : "avg_invocation_seconds"
      )
    }
    setLeaderboardPage(1)
    setLeaderboardCursors([undefined])
  }

  return (
    <>
      <PRMergeRateSection report={report} />

      <PageSection
        title="Agent leaderboard"
        description="Ranked by merged PRs, then agent lines of code and PRs opened."
        contained={!leaderboardFailed}
        actions={
          <ToggleGroup
            aria-label="Usage scope"
            value={[usageScope]}
            onValueChange={(values) => {
              const scope = values[0]
              if (isUsageScope(scope)) changeScope(scope)
            }}
          >
            {USAGE_SCOPES.map((scope) => (
              <ToggleGroupItem key={scope} value={scope} className="capitalize">
                {scope}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        }
      >
        {leaderboard.isLoading ? (
          <LoadingRows count={3} />
        ) : leaderboard.isError ? (
          <StateNotice
            tone="RISK"
            icon={AlertTriangle}
            title={
              leaderboard.error instanceof ApiError &&
              leaderboard.error.status === 503
                ? "Usage analytics is unavailable on this deployment."
                : "Could not load usage analytics"
            }
            description="The leaderboard and reviewer stats stay hidden until it loads."
            action={
              <Button
                type="button"
                size="compact"
                variant="outline"
                onClick={() => void leaderboard.refetch()}
              >
                Retry usage analytics
              </Button>
            }
          />
        ) : !leaderboard.data?.total_members ? (
          <EmptyState
            icon={Users}
            title="No agent usage yet"
            description={`No Open SWE Agent usage has been recorded for ${periodLabel} yet.`}
          />
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
      </PageSection>

      <PageSection
        title="Reviewer stats"
        description="Issues surfaced by Open SWE Review and how often users addressed them."
      >
        {leaderboard.isLoading ? (
          <Box className="grid gap-1 sm:grid-cols-3">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-24 w-full rounded-panel" />
            ))}
          </Box>
        ) : leaderboard.isError ? (
          <StateNotice
            tone="RISK"
            icon={AlertTriangle}
            title="Reviewer stats are unavailable"
            description="Retry usage analytics above."
          />
        ) : leaderboard.data?.reviewer_stats ? (
          <ReviewerStats stats={leaderboard.data.reviewer_stats} />
        ) : (
          <EmptyState
            icon={Bug}
            title="No reviewer stats yet"
            description={`No reviewer stats have been recorded for ${periodLabel} yet.`}
          />
        )}
      </PageSection>

      <AnalyticsCoverage
        reports={metadata}
        reportPayload={report.data?.payload ?? null}
        refreshing={refreshing}
        onRefresh={refreshNow}
        period={activePeriod}
        reportFetchedAt={report.data?.fetchedAt ?? null}
        reportServerAsOf={report.data?.payload.as_of ?? null}
        reportRefreshError={report.isError && !report.data ? null : reportError}
      />
    </>
  )
}

function AnalyticsCoverage({
  reports,
  reportPayload,
  refreshing,
  onRefresh,
  period,
  reportFetchedAt,
  reportServerAsOf,
  reportRefreshError,
}: {
  reports: AnalyticsMetadata[]
  /** The retained PR report payload; survives a failed refresh even when `reports` excludes it. */
  reportPayload: PRMergeRatePayload | null
  refreshing: boolean
  onRefresh: () => void
  period: UsageLeaderboardPeriod
  /** When this browser last received the PR report; separate from the server-side `as_of`. */
  reportFetchedAt: string | null
  /** The PR report's own server-side as_of; never another report's. */
  reportServerAsOf: string | null
  /** Failed manual refresh while the last good report stays on screen. */
  reportRefreshError: ApiError | null
}) {
  if (!reports.length) return null
  const latest = reports.reduce((a, b) => (a.as_of > b.as_of ? a : b))
  const hasPendingEvents = reports.some((data) => data.has_pending_events)
  const hasFailedEvents = reports.some((data) => data.has_failed_events)
  const status: {
    label: string
    description?: string
    icon: Glyph
    tone: string
  } = hasFailedEvents
    ? {
        label: "Analytics need attention",
        description:
          "Some events could not be processed. Reports may be incomplete.",
        icon: AlertCircle,
        tone: "text-risk",
      }
    : hasPendingEvents
      ? {
          label: "Analytics are updating",
          description: "New activity is still being processed.",
          icon: Clock,
          tone: "text-attention",
        }
      : {
          label: "Analytics are up to date",
          icon: CheckCircle,
          tone: "text-positive",
        }

  return (
    <Box
      role="status"
      aria-label="Analytics coverage"
      bg="panel"
      border="line"
      radius="panel"
      className="overflow-hidden"
    >
      <Collapsible>
        <Inline gap="md" align="center" className="px-4 py-3">
          <Icon icon={status.icon} size="md" className={status.tone} />
          <Stack gap="none" className="min-w-0 flex-1">
            <Box render={<span />} className="text-label font-medium text-ink">
              {status.label}
            </Box>
            {status.description ? (
              <Box render={<span />} className="text-meta text-ink-subtle">
                {status.description}
              </Box>
            ) : null}
          </Stack>
          <Button
            type="button"
            size="compact"
            variant="outline"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <Icon
              icon={RefreshCw}
              size="sm"
              className={
                refreshing ? "animate-spin motion-reduce:animate-none" : ""
              }
            />
            {refreshing ? "Refreshing…" : "Refresh now"}
          </Button>
          <CollapsibleTrigger className="text-label font-medium text-ink-subtle hover:text-ink data-panel-open:text-ink">
            Details
            <CollapsibleChevron />
          </CollapsibleTrigger>
        </Inline>
        <CollapsibleContent>
          <Stack
            gap="xs"
            className="border-t border-line px-4 py-3 text-meta text-ink-subtle"
          >
            <p>
              Period: {PERIOD_LABELS[period]} · PR report as of{" "}
              {reportServerAsOf ? (
                <time dateTime={reportServerAsOf}>
                  {new Date(reportServerAsOf).toLocaleString()}
                </time>
              ) : (
                "Unavailable"
              )}{" "}
              (server); last fetched by this browser:{" "}
              {reportFetchedAt
                ? new Date(reportFetchedAt).toLocaleString()
                : "Unavailable"}
              .
            </p>
            {reportRefreshError ? (
              <p className="text-risk">
                Last PR report refresh failed (
                {reportRefreshError.status > 0
                  ? `HTTP ${reportRefreshError.status}`
                  : "network error"}
                ). The report shown is from the last successful fetch above.
              </p>
            ) : null}
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
            </p>
            <CopyDiagnosticsButton
              getDiagnostics={() =>
                buildUsageDiagnostics({
                  period,
                  reports,
                  reportServerAsOf,
                  reportFetchedAt,
                  reportRefreshError,
                  avgDeliverySeconds: avgDeliveryAvailability(reportPayload),
                })
              }
            />
          </Stack>
        </CollapsibleContent>
      </Collapsible>
    </Box>
  )
}

/** Delivery-timing availability of the retained PR report, even while a refresh fails. */
function avgDeliveryAvailability(
  payload: PRMergeRatePayload | null
): MetricAvailability | null {
  if (!payload || payload.status !== "ready" || !payload.cohorts.length) {
    return null
  }
  const cohorts = payload.cohorts
  const supported = cohorts.some((cohort) => "avg_delivery_seconds" in cohort)
  const values = cohorts
    .map((cohort) =>
      "avg_delivery_seconds" in cohort ? cohort.avg_delivery_seconds : null
    )
    .filter((value): value is number => typeof value === "number")
  return metricAvailability(supported, values[0] ?? null)
}

function usePRMergeRateReport(
  period: UsageLeaderboardPeriod,
  login: string,
  isAdmin: boolean,
  onRefreshErrorChange: (error: ApiError | null) => void
) {
  return useQuery({
    queryKey: ["prMergeRateByModel", period, login, isAdmin],
    queryFn: (): Promise<PRMergeRateResponse> => api.prMergeRateByModel(period),
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status >= 400) && count < 2,
    // Manual refreshes are not the only successes: automatic interval/focus
    // fetches must also clear a retained failure announcement.
    meta: { onRefreshErrorChange },
  })
}

/** A footnote under a table: a quiet trigger that opens an explanation in place. */
function TableFootnote({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <Collapsible className="border-t border-line px-4 py-3">
      <CollapsibleTrigger className="text-meta text-ink-subtle hover:text-ink">
        <CollapsibleChevron />
        {label}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Stack gap="md" className="pt-3 text-meta text-ink-subtle">
          {children}
        </Stack>
      </CollapsibleContent>
    </Collapsible>
  )
}

function PRMergeRateSection({
  report,
}: {
  report: ReturnType<typeof usePRMergeRateReport>
}) {
  const data = report.data?.payload
  const failed = report.isError && !data
  const emptyMessage =
    data?.status === "not_started"
      ? "No analytics records have been captured since the reporting cutover yet."
      : data?.status === "suppressed"
        ? "PR groups in this period are too small to show under the privacy threshold."
        : "No PRs have been recorded for this period yet."

  return (
    <PageSection
      title="PR outcomes"
      description="Outcomes for PRs opened during the selected period."
      contained={!failed}
    >
      {report.isPending ? (
        <LoadingRows count={2} label="Loading PR outcomes" />
      ) : failed ? (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title={
            report.error instanceof ApiError && report.error.status === 503
              ? "PR analytics is unavailable on this deployment."
              : "Could not load PR outcomes"
          }
          description="No outcomes are shown until the report loads."
          action={
            <Button
              type="button"
              size="compact"
              variant="outline"
              onClick={() => void report.refetch()}
            >
              Retry
            </Button>
          }
        />
      ) : (
        <Stack gap="none">
          {data?.status === "ready" ? (
            <PRMergeRateTable
              key={data.period}
              cohorts={data.cohorts}
              maturityDays={data.maturity_days}
            />
          ) : (
            <EmptyState
              icon={GitPullRequest}
              title="No PR outcomes to show"
              description={emptyMessage}
            />
          )}
          {data?.unavailable_thread_ids.length ? (
            <TableFootnote
              label={`Unavailable model attribution (${data.unavailable_thread_ids.length})`}
            >
              <p>
                These PRs are excluded from model outcomes. Copy a thread ID to
                triage its opening-run attribution.
              </p>
              <Stack render={<ul />} gap="xs">
                {data.unavailable_thread_ids.map((threadId) => (
                  <Inline render={<li />} key={threadId} gap="sm" align="center">
                    <Box render={<code />} className="font-mono select-all">
                      {threadId}
                    </Box>
                    <Button
                      type="button"
                      size="compact"
                      variant="ghost"
                      onClick={() =>
                        void navigator.clipboard.writeText(threadId)
                      }
                    >
                      <Icon icon={Copy} size="sm" />
                      Copy
                    </Button>
                  </Inline>
                ))}
              </Stack>
            </TableFootnote>
          ) : null}
          {data ? (
            <TableFootnote label="How these numbers work">
              <p>
                <strong className="font-medium text-ink">Open</strong>{" "}
                includes PRs that haven’t been merged or closed. Each count’s
                tooltip shows the age breakdown: open for less than{" "}
                {data.maturity_days} days, or open for {data.maturity_days}{" "}
                days or longer.
              </p>
              <p>
                <strong className="font-medium text-ink">Median distance</strong>{" "}
                is the median normalized line edit distance between each merged
                PR’s opening diff and final diff. It is calculated only for
                merged PRs with complete text patches; higher means more
                post-open editing.{" "}
                <strong className="font-medium text-ink">Mean distance</strong>{" "}
                is the arithmetic mean of those same per-PR percentages;
                unusually rewritten PRs affect it more. Neither metric weights
                PRs by size. The measured/merged count shows how many merged PRs
                had complete patches.
              </p>
              <p>
                <strong className="font-medium text-ink">Merge rate</strong>{" "}
                includes only PRs old enough to have a meaningful outcome. It
                counts merged, closed without merge, and still-open PRs that are
                at least {data.maturity_days} days old. Newer open PRs are
                excluded so they do not lower the rate before they have had
                enough time to merge.
              </p>
              <p>
                <strong className="font-medium text-ink">Time to merge</strong>{" "}
                is the arithmetic mean of time from PR opened to merged across
                merged PRs opened in the selected period. Unmerged PRs are
                excluded, and it shows — when a group has no merges.
              </p>
              <p>
                <strong className="font-medium text-ink">Time to PR</strong> is
                the arithmetic mean of time from opening-run start to PR
                creation across all PRs opened in the selected period. PRs
                without a valid opening-run start time are excluded, and it
                shows — when a group has none.
              </p>
              <Stack render={<ul />} gap="xs" className="list-disc pl-4">
                <li>
                  Merge rate = merged ÷ (merged + closed without merge + open
                  at least {data.maturity_days} days).
                </li>
              </Stack>
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
            </TableFootnote>
          ) : null}
        </Stack>
      )}
    </PageSection>
  )
}

function AvgTimeToMerge({
  cohort,
}: {
  cohort: PRMergeRateCohort | PRMergeRateEffort
}) {
  return (
    <span>
      {cohort.avg_merge_seconds == null
        ? "—"
        : formatAvgDuration(cohort.avg_merge_seconds)}
    </span>
  )
}

function AvgTimeToPR({
  cohort,
}: {
  cohort: PRMergeRateCohort | PRMergeRateEffort
}) {
  if (!("avg_delivery_seconds" in cohort)) {
    // A backend that predates the metric has no key for it at all.
    return (
      <span
        className={HINT_TRIGGER_CLASS}
        title="Metric unavailable from this backend"
        aria-label="Time to PR: metric unavailable from this backend"
        tabIndex={0}
      >
        —
      </span>
    )
  }
  if (cohort.avg_delivery_seconds == null) {
    return <span title="No PRs with valid timing in this group">—</span>
  }
  return <span>{formatAvgDuration(cohort.avg_delivery_seconds)}</span>
}

type OutcomeGroup = Pick<
  PRMergeRateCohort,
  | "cohort_size"
  | "merged"
  | "closed_without_merge"
  | "mature_pending"
  | "waiting"
>
type Outcome = "merged" | "closed_without_merge" | "open"

function outcomePercent(group: OutcomeGroup, outcome: Outcome): number {
  if (!group.cohort_size) return 0
  const counts = [
    group.merged,
    group.closed_without_merge,
    group.mature_pending + group.waiting,
  ]
  const raw = counts.map((count) => (count * 100) / group.cohort_size)
  const rounded = raw.map(Math.floor)
  const remainder = 100 - rounded.reduce((sum, value) => sum + value, 0)
  const order = [0, 1, 2].sort(
    (a, b) => raw[b]! - rounded[b]! - (raw[a]! - rounded[a]!) || a - b
  )
  for (let index = 0; index < remainder; index++) rounded[order[index]!]!++
  return rounded[{ merged: 0, closed_without_merge: 1, open: 2 }[outcome]]!
}

function OutcomeCell({
  group,
  outcome,
  maturityDays,
}: {
  group: OutcomeGroup
  outcome: Outcome
  maturityDays: number
}) {
  const [open, setOpen] = useState(false)
  const count =
    outcome === "open" ? group.mature_pending + group.waiting : group[outcome]
  const value = (
    <>
      {count}{" "}
      <span className="text-ink-subtle">
        ({outcomePercent(group, outcome)}%)
      </span>
    </>
  )
  if (outcome !== "open" || !count) return value
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        closeOnClick={false}
        onClick={() => setOpen(true)}
        className={HINT_TRIGGER_CLASS}
      >
        {value}
      </TooltipTrigger>
      <TooltipContent className="flex-col items-start gap-0.5">
        <div>
          {group.waiting} open for less than {maturityDays} days
        </div>
        <div>
          {group.mature_pending} open for {maturityDays} days or longer
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

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
    case "mean_distance":
      return cohort.mean_distance_basis_points ?? null
    case "merge_rate":
      return cohort.mature_cohort_merge_share
    case "avg_delivery_seconds":
      return cohort.avg_delivery_seconds ?? null
    case "avg_merge_seconds":
      return cohort.avg_merge_seconds ?? null
  }
}

function prOutcomeColumns(
  maturityDays: number
): Array<SortableColumn<PROutcomesSort>> {
  return [
    {
      key: "model",
      label: "Opening model",
      align: "left",
      defaultDirection: "asc",
    },
    { key: "prs_opened", label: "PRs opened (base)", align: "right" },
    { key: "merged", label: "Merged", align: "right" },
    {
      key: "merge_rate",
      label: "Mature merge rate",
      align: "right",
      tooltip: `Includes merged and closed PRs, plus PRs open for at least ${maturityDays} days. Newer open PRs are excluded.`,
    },
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
        "Median post-open line edit distance across measurable merged PRs.",
    },
    {
      key: "mean_distance",
      label: "Mean distance",
      align: "right",
      tooltip:
        "Mean post-open line edit distance across measurable merged PRs. Unusually rewritten PRs affect it more than the median; PRs are not weighted by size.",
    },
    {
      key: "avg_delivery_seconds",
      label: "Time to PR",
      align: "right",
      tooltip:
        "Average time from opening-run start to PR creation, regardless of outcome. PRs with missing or invalid timing are excluded.",
    },
    {
      key: "avg_merge_seconds",
      label: "Time to merge",
      align: "right",
      tooltip:
        "Average time from PR opened to merged. Unmerged PRs are excluded.",
    },
  ]
}

function SmallSample() {
  return <Box className="font-sans text-meta text-attention">Small sample</Box>
}

function PROutcomeCells({
  group,
  maturityDays,
}: {
  group: PRMergeRateCohort | PRMergeRateEffort
  maturityDays: number
}) {
  const rate = group.mature_cohort_merge_share
  return (
    <>
      <TableCell className={NUMBER_CELL_CLASS}>
        {group.cohort_size} <span className="text-ink-subtle">(100%)</span>
      </TableCell>
      {(["merged", "closed_without_merge", "open"] as const).map((outcome) => (
        <Fragment key={outcome}>
          <TableCell className={NUMBER_CELL_CLASS}>
            <OutcomeCell
              group={group}
              outcome={outcome}
              maturityDays={maturityDays}
            />
          </TableCell>
          {outcome === "merged" && (
            <TableCell className={NUMBER_CELL_CLASS}>
              <span className="font-medium">
                {rate == null ? "—" : formatPercent(rate)}
              </span>
              {rate != null && (
                <Box className="text-meta text-ink-subtle">
                  {group.merged}/{group.mature_denominator} eligible
                </Box>
              )}
              {rate != null && group.mature_denominator < 5 && <SmallSample />}
            </TableCell>
          )}
        </Fragment>
      ))}
      {(["median_distance", "mean_distance"] as const).map((metric) => {
        const value =
          metric === "median_distance"
            ? group.median_distance_basis_points
            : "mean_distance_basis_points" in group
              ? group.mean_distance_basis_points
              : undefined
        const supported =
          metric === "median_distance" || "mean_distance_basis_points" in group
        return (
          <TableCell key={metric} className={NUMBER_CELL_CLASS}>
            <Tooltip>
              <TooltipTrigger className={HINT_TRIGGER_CLASS}>
                {value == null ? "—" : `${(value / 100).toFixed(1)}%`}
              </TooltipTrigger>
              <TooltipContent>
                {supported
                  ? `${group.distance_sample_size ?? 0}/${group.merged} merged PRs measured`
                  : "Mean distance unavailable for this group"}
              </TooltipContent>
            </Tooltip>
            {supported && group.distance_sample_size !== undefined && (
              <Box className="text-meta text-ink-subtle">
                {group.distance_sample_size}/{group.merged} measured
              </Box>
            )}
            {supported &&
              (group.distance_sample_size ?? 0) > 0 &&
              (group.distance_sample_size ?? 0) < 5 && <SmallSample />}
          </TableCell>
        )
      })}
      <TableCell className={NUMBER_CELL_CLASS}>
        <AvgTimeToPR cohort={group} />
      </TableCell>
      <TableCell className={cn(NUMBER_CELL_CLASS, "pr-4")}>
        <AvgTimeToMerge cohort={group} />
      </TableCell>
    </>
  )
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
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [sort, setSort] = useState<PROutcomesSort>("prs_opened")
  const [direction, setDirection] = useState<SortDirection>("desc")
  const sortedCohorts = [...cohorts].sort((a, b) => {
    const aValue = prOutcomeSortValue(a, sort)
    const bValue = prOutcomeSortValue(b, sort)
    if (aValue == null) return bValue == null ? 0 : 1
    if (bValue == null) return -1
    const comparison =
      typeof aValue === "string" && typeof bValue === "string"
        ? aValue.localeCompare(bValue, undefined, { sensitivity: "base" })
        : Number(aValue) - Number(bValue)
    return (
      (direction === "asc" ? comparison : -comparison) ||
      (safeModelLabel(a.model_id ?? "") || "Unavailable").localeCompare(
        safeModelLabel(b.model_id ?? "") || "Unavailable"
      )
    )
  })
  const pageCount = Math.max(1, Math.ceil(sortedCohorts.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const rows = sortedCohorts.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  )
  const columns = prOutcomeColumns(maturityDays)

  return (
    <Stack gap="none">
      <Table className="text-label">
        <TableCaption className="mt-0 caption-top px-4 py-3 text-left">
          Outcome shares use PRs opened in each row as their base. Expand a
          model to see its reasoning efforts.
        </TableCaption>
        <TableHeader>
          <TableRow>
            {columns.map((column, index) => (
              <SortableHeader
                key={column.key}
                column={column}
                sortKey={sort}
                sortDirection={direction}
                onSort={(nextSort, defaultDirection) => {
                  setDirection(
                    nextSort === sort
                      ? direction === "asc"
                        ? "desc"
                        : "asc"
                      : defaultDirection
                  )
                  setSort(nextSort)
                  setPage(1)
                }}
                className={cn(
                  column.key === "model" && cn(PINNED_CELL_CLASS, "pl-2"),
                  index === columns.length - 1 && "pr-2"
                )}
              />
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((cohort) => {
            const key = `${cohort.model_id}-${cohort.model_attribution_quality}`
            const modelLabel =
              safeModelLabel(cohort.model_id ?? "") || "Unavailable"
            const hasMultipleEfforts = cohort.efforts.length > 1
            const isExpanded = hasMultipleEfforts && expanded.has(key)
            return (
              <Fragment key={key}>
                <TableRow>
                  <TableHead
                    scope="row"
                    className={cn(
                      PINNED_CELL_CLASS,
                      "py-2 pl-4 font-normal text-ink"
                    )}
                  >
                    <Inline gap="sm" align="center">
                      {hasMultipleEfforts ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
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
                          <Icon
                            icon={isExpanded ? ChevronDown : ChevronRight}
                            size="sm"
                            className="text-ink-subtle"
                          />
                        </Button>
                      ) : (
                        <Box
                          aria-hidden="true"
                          className="h-control-sm w-control-sm shrink-0"
                        />
                      )}
                      <Box>
                        <Box className="font-medium">{modelLabel}</Box>
                        <Box className="text-meta text-ink-subtle">
                          {hasMultipleEfforts
                            ? "All efforts"
                            : formatEffort(cohort.efforts[0]?.effort)}{" "}
                          · {cohort.model_attribution_quality} attribution
                        </Box>
                      </Box>
                    </Inline>
                  </TableHead>
                  <PROutcomeCells group={cohort} maturityDays={maturityDays} />
                </TableRow>
                {isExpanded &&
                  cohort.efforts.map((effort) => (
                    <TableRow key={`${key}-${effort.effort ?? "unknown"}`}>
                      <TableHead
                        scope="row"
                        className={cn(
                          PINNED_CELL_CLASS,
                          "py-2 pl-13 font-medium text-ink"
                        )}
                      >
                        {formatEffort(effort.effort)}
                      </TableHead>
                      <PROutcomeCells
                        group={effort}
                        maturityDays={maturityDays}
                      />
                    </TableRow>
                  ))}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
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
    </Stack>
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
    { key: "prs_reviewed", label: "PRs reviewed", align: "right" },
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
  className?: string
}) {
  const isActive = sortKey === column.key
  const glyph = isActive
    ? sortDirection === "asc"
      ? SortAsc
      : SortDesc
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
      className={cn(
        "flex h-row-data w-full items-center gap-1 px-2 outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset",
        column.align === "right" ? "justify-end" : "justify-start",
        isActive && "text-ink",
        column.tooltip &&
          "cursor-help underline decoration-dotted underline-offset-2"
      )}
    >
      {column.label}
      <Icon
        icon={glyph}
        size="sm"
        className={isActive ? undefined : "text-line-strong"}
      />
    </button>
  )

  return (
    <TableHead
      scope="col"
      aria-sort={ariaSort}
      className={cn(
        "p-0",
        column.align === "right" ? "text-right" : "text-left",
        className
      )}
    >
      {column.tooltip ? (
        <Tooltip>
          <TooltipTrigger render={button} />
          <TooltipContent>{column.tooltip}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}
    </TableHead>
  )
}

function formatEffort(effort: string | null | undefined) {
  return effort
    ? effort.charAt(0).toUpperCase() + effort.slice(1)
    : "Unknown / legacy"
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
  const columns = usageColumns(scope, period)
  return (
    <Stack gap="none">
      <Table aria-busy={isUpdating} className="text-label">
        {isUpdating ? (
          <TableCaption className="sr-only">Updating leaderboard</TableCaption>
        ) : null}
        <TableHeader>
          <TableRow>
            {columns.map((column, index) => (
              <SortableHeader
                key={column.key}
                column={column}
                sortKey={sort}
                sortDirection={direction}
                onSort={onSort}
                className={cn(
                  index === 0 && "w-14 pl-2",
                  index === columns.length - 1 && "pr-2",
                  column.key === "user" && PINNED_CELL_CLASS
                )}
              />
            ))}
          </TableRow>
        </TableHeader>
        <TableBody
          className={cn(
            "transition-opacity duration-fast ease-out-quint motion-reduce:transition-none",
            isUpdating && "opacity-50"
          )}
        >
          {rows.map((row) => (
            <TableRow
              key={`${row.rank}-${row.user.github_login ?? row.user.email ?? row.user.name}`}
            >
              <TableCell className="py-2 pl-4 font-mono text-ink-subtle tabular-nums">
                {row.rank}
              </TableCell>
              <TableCell className={cn(PINNED_CELL_CLASS, "py-2")}>
                <UserCell
                  row={row}
                  isCurrentUser={row.rank === currentUserRank}
                />
              </TableCell>
              <TableCell className="max-w-48 py-2 text-ink-subtle">
                <Box className="truncate">
                  {safeModelLabel(row.favorite_model) || "Unavailable"}
                </Box>
                <Box className="text-meta capitalize">
                  {row.favorite_model_effort === undefined
                    ? null
                    : (row.favorite_model_effort ?? "Unknown")}
                </Box>
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {formatNumber(
                  scope === "threads" ? (row.threads ?? 0) : row.invocations
                )}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {row.threads
                  ? (row.invocations / row.threads).toFixed(1)
                  : "—"}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {formatNumber(row.total_tokens)}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                <UsageCost row={row} />
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {formatDuration(
                  scope === "threads"
                    ? (row.avg_thread_seconds ?? 0)
                    : row.avg_invocation_seconds
                )}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {formatNumber(row.prs_opened)}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {formatNumber(row.merged_prs)}
              </TableCell>
              <TableCell className={NUMBER_CELL_CLASS}>
                {(row.merged_prs_per_thread ?? 0).toFixed(2)}
              </TableCell>
              <TableCell
                className={NUMBER_CELL_CLASS}
                title={`${formatNumber(row.additions)} additions, ${formatNumber(row.deletions)} deletions`}
              >
                {formatNumber(row.agent_loc)}
              </TableCell>
              <TableCell className={cn(NUMBER_CELL_CLASS, "pr-4")}>
                {formatNumber(row.prs_reviewed ?? 0)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={totalMembers}
        disabled={isUpdating}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </Stack>
  )
}

function ReviewerStats({ stats }: { stats: ReviewerStatsPayload }) {
  const cards: Array<{
    title: string
    icon: Glyph
    value: number
    detail: string
  }> = [
    {
      title: "Reviewed PRs",
      icon: GitPullRequest,
      value: stats.reviewed_prs,
      detail: `${formatNumber(stats.prs_with_findings)} with findings`,
    },
    {
      title: "Issues surfaced",
      icon: Bug,
      value: stats.surfaced_findings,
      detail: `${formatNumber(stats.findings_recorded)} recorded`,
    },
    {
      title: "Addressed & resolved",
      icon: CheckCircle,
      value: stats.addressed_findings,
      detail: `${formatPercent(stats.resolution_rate)} of surfaced`,
    },
    {
      title: "Resolved after update",
      icon: RefreshCw,
      value: stats.resolved_after_update,
      detail: "Resolved on a later PR head",
    },
    {
      title: "Awaiting follow-up",
      icon: Clock,
      value: stats.unresolved_surfaced_findings,
      detail: "Surfaced but not resolved/dismissed",
    },
    {
      title: "Dismissed",
      icon: MinusCircle,
      value: stats.dismissed_findings,
      detail: `${formatNumber(stats.human_replies)} human replies tracked`,
    },
  ]

  return (
    <Stack gap="xl">
      <MetricCardGrid label="Reviewer stats" columns={3}>
        {cards.map((card) => (
          <MetricCard
            key={card.title}
            title={card.title}
            icon={card.icon}
            value={formatNumber(card.value)}
            detail={card.detail}
          />
        ))}
      </MetricCardGrid>
      <Box className="grid gap-6 sm:grid-cols-2">
        <CounterList title="Top categories" icon={BarChart} rows={stats.top_categories} />
        <CounterList title="Severity mix" icon={AlertCircle} rows={severityRows(stats)} />
      </Box>
    </Stack>
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
  icon,
  rows,
}: {
  title: string
  icon: Glyph
  rows: Array<{ name: string; count: number }>
}) {
  return (
    <Stack gap="xs" className="min-w-0">
      <Inline gap="sm" align="center">
        <Icon icon={icon} size="sm" className="text-ink-subtle" />
        <Box render={<h3 />} className="text-label font-medium text-ink">
          {title}
        </Box>
      </Inline>
      {rows.length ? (
        <Stack gap="none">
          {rows.map((row) => (
            <StatReadout
              key={row.name}
              shape="field"
              label={row.name}
              value={formatNumber(row.count)}
            />
          ))}
        </Stack>
      ) : (
        <Box render={<p />} className="text-meta text-ink-subtle">
          No data yet.
        </Box>
      )}
    </Stack>
  )
}

function UserCell({
  row,
  isCurrentUser,
}: {
  row: Pick<UsageLeaderboardRow, "user">
  isCurrentUser: boolean
}) {
  const detail = row.user.github_login
  const profileUrl = githubProfileUrl(row.user.github_login)
  const name = profileUrl ? (
    <a
      href={profileUrl}
      target="_blank"
      rel="noreferrer"
      className="truncate rounded-tick font-medium text-ink underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-primary"
    >
      {row.user.name}
    </a>
  ) : (
    <span className="truncate font-medium text-ink">{row.user.name}</span>
  )
  const avatar = (
    <Avatar
      name={row.user.name}
      src={row.user.avatar_url ?? undefined}
      size="control"
    />
  )
  return (
    <Inline gap="sm" align="center" className="min-w-0">
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
      <Stack gap="none" className="min-w-0">
        <Inline gap="sm" align="center" className="min-w-0">
          {name}
          {isCurrentUser ? (
            <Badge tier="quiet" tone="info" aria-label="You">
              You
            </Badge>
          ) : null}
        </Inline>
        {detail && detail !== row.user.name ? (
          <span className="truncate text-meta text-ink-subtle">{detail}</span>
        ) : null}
      </Stack>
    </Inline>
  )
}

function githubProfileUrl(login: string | null): string | null {
  if (!login) return null
  return `https://github.com/${encodeURIComponent(login)}`
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
    <Stack gap="none" align="end">
      <span>{unavailable ? "—" : formatCurrency(row.total_cost_usd)}</span>
      <Tooltip>
        <TooltipTrigger
          aria-label={`Cost ${label.toLowerCase()}`}
          className={cn(HINT_TRIGGER_CLASS, "font-sans text-meta text-ink-subtle")}
        >
          {label}
        </TooltipTrigger>
        <TooltipContent>{explanation}</TooltipContent>
      </Tooltip>
    </Stack>
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
