/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type PRMergeRatePayload,
  type UsageLeaderboardPayload,
} from "@/lib/api"

import { UsageAnalytics } from "./usage"

const captured: PRMergeRatePayload = {
  status: "no_prs",
  metric: "pr_outcomes_by_opening_invocation_configured_model",
  definition: "PR outcomes",
  maturity_days: 21,
  period: "30d",
  suppression_threshold: 5,
  cohorts: [],
  reporting_cutover_at: "2026-09-11T11:00:00Z",
  collection_started_at: "2026-09-11T12:00:00Z",
  last_processed_at: null,
  data_source: "event_projections",
  completeness: "observed_events_only",
  has_pending_events: true,
  has_failed_events: false,
  as_of: "2026-09-11T12:01:00Z",
}

const emptyUsage: UsageLeaderboardPayload = {
  ...captured,
  has_pending_events: false,
  rows: [],
  total_members: 0,
  current_user_rank: null,
  generated_at_ms: null,
  reviewer_stats: {
    period: "30d",
    reviewed_prs: 0,
    prs_with_findings: 0,
    findings_recorded: 0,
    surfaced_findings: 0,
    addressed_findings: 0,
    resolved_after_update: 0,
    dismissed_findings: 0,
    unresolved_surfaced_findings: 0,
    resolution_rate: 0,
    human_replies: 0,
    severity_counts: {},
    top_categories: [],
    generated_at_ms: null,
  },
}

beforeEach(() => {
  vi.spyOn(api, "usageLeaderboard").mockResolvedValue(emptyUsage)
})

function mountReport() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <UsageAnalytics
        period="30d"
        login="reader"
        isAdmin={false}
        onPeriodChange={() => {}}
      />
    </QueryClientProvider>
  )
  return client
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("shows delivery lag separately from suppression, then refreshes to a populated report", async () => {
  const query = vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  const client = mountReport()
  expect(await screen.findByText(/No PRs have been recorded/)).toBeTruthy()
  expect(screen.getByText(/still waiting to be processed/)).toBeTruthy()
  expect(screen.getByText(/Reporting since/)).toBeTruthy()
  expect(
    screen
      .getByText(new Date(captured.reporting_cutover_at).toLocaleString())
      .getAttribute("datetime")
  ).toBe(captured.reporting_cutover_at)
  expect(screen.queryByText(/too small to show/)).toBeNull()

  query.mockResolvedValue({
    ...captured,
    status: "ready",
    last_processed_at: "2026-09-11T12:02:00Z",
    has_pending_events: false,
    cohorts: [
      {
        model_id: "example-model",
        model_attribution_quality: "configured",
        merged: 3,
        closed_without_merge: 1,
        mature_pending: 1,
        waiting: 0,
        cohort_size: 5,
        decided_denominator: 4,
        decided_merge_rate: 0.75,
        mature_denominator: 5,
        mature_cohort_merge_share: 0.6,
      },
    ],
  })
  await act(() => client.invalidateQueries())
  expect(await screen.findByText("example-model")).toBeTruthy()
  expect(screen.queryByText(/still waiting to be processed/)).toBeNull()
  expect(screen.getByText(/Last event processed/)).toBeTruthy()
  client.clear()
})

it("offers recovery from unavailability without claiming an empty or suppressed report", async () => {
  const query = vi
    .spyOn(api, "prMergeRateByModel")
    .mockRejectedValue(new ApiError(503, "unavailable"))
  const client = mountReport()
  expect(await screen.findByRole("alert")).toBeTruthy()
  expect(
    screen.queryByText(/No PRs have been recorded|too small to show/)
  ).toBeNull()
  expect(screen.getAllByLabelText("Analytics coverage")).toHaveLength(1)

  query.mockResolvedValue({
    ...captured,
    status: "not_started",
    collection_started_at: null,
    completeness: "not_started",
    has_pending_events: false,
  })
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))
  expect(
    await screen.findByText(
      /No analytics records have been captured since the reporting cutover/
    )
  ).toBeTruthy()
  expect(screen.queryByRole("alert")).toBeNull()
  client.clear()
})

it("keeps failed delivery visible when all PR groups are suppressed", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    status: "suppressed",
    has_pending_events: false,
    has_failed_events: true,
  })
  const client = mountReport()
  expect(await screen.findByText(/too small to show/)).toBeTruthy()
  expect(screen.getByText(/Some events could not be processed/)).toBeTruthy()
  expect(screen.queryByRole("table")).toBeNull()
  expect(screen.queryByText(/No PRs have been recorded/)).toBeNull()
  client.clear()
})

it("distinguishes unavailable usage from empty usage and recovers without duplicate coverage notices", async () => {
  vi.mocked(api.usageLeaderboard).mockRejectedValue(
    new ApiError(503, "unavailable")
  )
  vi.spyOn(api, "prMergeRateByModel").mockRejectedValue(
    new ApiError(503, "unavailable")
  )
  const client = mountReport()
  expect(
    await screen.findByText(
      "Usage analytics is unavailable on this deployment."
    )
  ).toBeTruthy()
  expect(screen.queryByText(/No Open SWE Agent usage/)).toBeNull()
  expect(screen.queryByText("Reviewed PRs")).toBeNull()
  expect(screen.queryByLabelText("Analytics coverage")).toBeNull()

  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    has_pending_events: true,
  })
  fireEvent.click(screen.getByRole("button", { name: "Retry usage analytics" }))
  expect(await screen.findByText(/No Open SWE Agent usage/)).toBeTruthy()
  expect(screen.getAllByLabelText("Analytics coverage")).toHaveLength(1)
  expect(screen.getByText(/Reporting since/)).toBeTruthy()
  expect(screen.getByText(/still waiting to be processed/)).toBeTruthy()
  expect(screen.getByText("Reviewed PRs")).toBeTruthy()
  expect(
    screen.queryByText("Usage analytics is unavailable on this deployment.")
  ).toBeNull()
  client.clear()
})

it("shows usage metrics but removes stale results when a refresh becomes unavailable", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    generated_at_ms: Date.parse(captured.as_of),
    rows: [
      {
        rank: 1,
        user: { name: "Example Reader", github_login: "reader", email: null },
        favorite_model: "configured-model",
        invocations: 12,
        prs_opened: 8,
        merged_prs: 4,
        agent_loc: 35,
        additions: 50,
        deletions: 15,
        total_tokens: 1234,
        total_cost_usd: 2.5,
        avg_invocation_seconds: 90,
      },
    ],
    reviewer_stats: { ...emptyUsage.reviewer_stats, human_replies: 7 },
  })
  const client = mountReport()
  expect(await screen.findByText("Example Reader")).toBeTruthy()
  expect(screen.getByText("configured-model")).toBeTruthy()
  expect(screen.getByText("1,234")).toBeTruthy()
  expect(screen.getByText("$2.50")).toBeTruthy()
  expect(screen.getByText("2m")).toBeTruthy()
  expect(screen.getByTitle("50 additions, 15 deletions").textContent).toBe("35")
  expect(screen.getByText("7 human replies tracked")).toBeTruthy()

  vi.mocked(api.usageLeaderboard).mockRejectedValue(
    new ApiError(503, "unavailable")
  )
  await act(() => client.invalidateQueries({ queryKey: ["usageLeaderboard"] }))
  expect(
    await screen.findByText(
      "Usage analytics is unavailable on this deployment."
    )
  ).toBeTruthy()
  expect(screen.queryByText("Example Reader")).toBeNull()
  expect(screen.queryByText(/Updated /)).toBeNull()
  expect(screen.queryByText("7 human replies tracked")).toBeNull()
  client.clear()
})
