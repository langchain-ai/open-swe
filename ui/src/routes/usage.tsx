import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type {
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
import { api } from "@/lib/api"
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

  const leaderboard = useQuery({
    queryKey: ["usageLeaderboard", activePeriod],
    queryFn: () => api.usageLeaderboard(activePeriod, 10),
    enabled: !!session.data,
    staleTime: 5 * 60 * 1000,
  })

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  return (
    <AppShell user={session.data} title="Usage" className="max-w-5xl">
      <SettingsSection
        title="Agent leaderboard"
        description="Ranked by agent lines of code, then PRs opened and agent runs."
        action={
          <Select
            value={activePeriod}
            onValueChange={(value) =>
              navigate({
                to: "/usage",
                search: { period: value as UsageLeaderboardPeriod },
              })
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
          <p className="p-4 text-xs text-destructive">
            Failed to load usage data: {leaderboard.error.message}
          </p>
        ) : !leaderboard.data?.rows.length ? (
          <div className="p-6 text-center text-sm text-muted-foreground">
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
            Failed to load reviewer stats: {leaderboard.error.message}
          </p>
        ) : leaderboard.data?.reviewer_stats ? (
          <ReviewerStats stats={leaderboard.data.reviewer_stats} />
        ) : (
          <div className="p-6 text-center text-sm text-muted-foreground">
            No reviewer stats have been recorded for{" "}
            {PERIOD_LABELS[activePeriod].toLowerCase()} yet.
          </div>
        )}
      </SettingsSection>
    </AppShell>
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
      <table className="w-full min-w-[760px] text-sm">
        <thead className="border-b border-border text-xs text-muted-foreground">
          <tr>
            <th className="w-14 px-4 py-3 text-left font-normal">Rank</th>
            <th className="px-2 py-3 text-left font-normal">User</th>
            <th className="px-2 py-3 text-left font-normal">Favorite Model</th>
            <th className="px-2 py-3 text-right font-normal">Agent Runs</th>
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
                {formatNumber(row.agent_runs)}
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
            <div className="mt-1 text-xl font-medium tabular-nums">
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
        <ul className="mt-2 space-y-2 text-sm">
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

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value)
}

function formatPercent(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
    style: "percent",
  }).format(value)
}
