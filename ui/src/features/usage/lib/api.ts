/** Dashboard endpoint behind the usage page: the agent leaderboard and the
 * reviewer's aggregate finding outcomes. */

import { request } from "@/lib/apiClient"

export type UsageLeaderboardPeriod = "7d" | "30d" | "all"

export interface UsageLeaderboardRow {
  rank: number
  user: {
    name: string
    github_login: string | null
    email: string | null
  }
  favorite_model: string
  agent_runs: number
  prs_opened: number
  merged_prs: number
  agent_loc: number
  additions: number
  deletions: number
}

export interface ReviewerStatsCounterRow {
  name: string
  count: number
}

export interface ReviewerStatsPayload {
  period: UsageLeaderboardPeriod
  reviewed_prs: number
  prs_with_findings: number
  findings_recorded: number
  surfaced_findings: number
  addressed_findings: number
  resolved_after_update: number
  dismissed_findings: number
  unresolved_surfaced_findings: number
  resolution_rate: number
  human_replies: number
  severity_counts: Record<string, number>
  top_categories: Array<ReviewerStatsCounterRow>
  generated_at_ms: number | null
}

export interface UsageLeaderboardPayload {
  period: UsageLeaderboardPeriod
  rows: Array<UsageLeaderboardRow>
  total_members: number
  current_user_rank: number | null
  generated_at_ms: number | null
  reviewer_stats: ReviewerStatsPayload
}

export const usageApi = {
  leaderboard: (period: UsageLeaderboardPeriod = "30d", limit = 10) =>
    request<UsageLeaderboardPayload>(
      `/agent-usage-leaderboard?period=${encodeURIComponent(period)}&limit=${limit}`
    ),
}
