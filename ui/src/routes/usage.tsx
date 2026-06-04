import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type { UsageLeaderboardPeriod, UsageLeaderboardRow } from "@/lib/api"
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
    <AppShell
      user={session.data}
      title="Usage Leaderboard"
      description="Open SWE Agent usage recorded from this release onward. Reviewer-agent runs are excluded."
    >
      <SettingsSection
        title="Leaderboard"
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
            Failed to load usage leaderboard: {leaderboard.error.message}
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
