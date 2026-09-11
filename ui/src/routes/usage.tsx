import { createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type {
  AnalyticsMetadata,
  PRMergeRateCohort,
  ReviewerStatsPayload,
  UsageLeaderboardPeriod,
  UsageLeaderboardRow,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/usage")({
  validateSearch: (search: Record<string, unknown>) => ({
    period: typeof search.period === "string" ? search.period : undefined,
  }),
  component: UsagePage,
})

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
  const leaderboard = useQuery({
    queryKey: ["usageLeaderboard", activePeriod, login, isAdmin],
    queryFn: () => api.usageLeaderboard(activePeriod, 10),
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
        description="Ranked by merged PRs, then agent lines of code, PRs opened, and invocations. A thread can contain multiple invocations."
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
        ) : !leaderboard.data?.rows.length ? (
          <div className="p-6 text-center text-xs text-muted-foreground">
            No Open SWE Agent usage has been recorded for{" "}
            {PERIOD_LABELS[activePeriod].toLowerCase()} yet.
          </div>
        ) : (
          <UsageTable
            rows={leaderboard.data.rows}
            totalMembers={leaderboard.data.total_members}
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
        . All time starts at this cutover. Earlier Store history is not
        included.
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
      title="PR outcomes by opening invocation's configured model"
      description="PRs are grouped by open date and the opening invocation's configured model. Routing, provider fallback, subagents, and later invocations may use other models, so this does not measure one model's independent success."
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
        <PRMergeRateTable cohorts={data.cohorts} />
      ) : (
        <p
          className="p-6 text-center text-xs text-muted-foreground"
          role="status"
        >
          {emptyMessage}
        </p>
      )}
      {data ? (
        <div className="space-y-2 border-t border-border px-4 py-3 text-xs text-muted-foreground">
          <p>
            Open PRs become mature pending after {data.maturity_days} days;
            pending is never failure. Decided rate excludes pending PRs. Mature
            share includes mature pending PRs. Groups smaller than{" "}
            {data.suppression_threshold} PRs are withheld.
          </p>
        </div>
      ) : null}
    </SettingsSection>
  )
}

function PRMergeRateTable({ cohorts }: { cohorts: PRMergeRateCohort[] }) {
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
          {cohorts.map((cohort) => (
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
    </div>
  )
}

function UsageTable({
  rows,
  totalMembers,
}: {
  rows: Array<UsageLeaderboardRow>
  totalMembers: number
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1040px] text-xs">
        <thead className="border-b border-border text-xs text-muted-foreground">
          <tr>
            <th className="w-14 px-4 py-3 text-left font-normal">Rank</th>
            <th className="px-2 py-3 text-left font-normal">User</th>
            <th className="px-2 py-3 text-left font-normal">Favorite Model</th>
            <th className="px-2 py-3 text-right font-normal">Invocations</th>
            <th className="px-2 py-3 text-right font-normal">Tokens</th>
            <th className="px-2 py-3 text-right font-normal">Cost</th>
            <th className="px-2 py-3 text-right font-normal">
              Avg Invocation Duration
            </th>
            <th className="px-2 py-3 text-right font-normal">PRs Opened</th>
            <th className="px-2 py-3 text-right font-normal">Merged PRs</th>
            <th className="px-4 py-3 text-right font-normal">Agent LOC</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
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
                {formatCurrency(row.total_cost_usd)}
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
      <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
        Top {Math.min(10, totalMembers)} of {formatNumber(totalMembers)} member
        {totalMembers === 1 ? "" : "s"}
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
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Avatar>
        <AvatarFallback>{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-foreground">
          {row.user.name}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {row.user.email ?? row.user.github_login ?? "unknown"}
        </span>
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
