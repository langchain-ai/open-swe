/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type PRMergeRatePayload,
  type UsageLeaderboardPayload,
  type UsageLeaderboardRow,
} from "@/lib/api"
import { TooltipProvider } from "@/components/ui/tooltip"

import { UsageAnalytics } from "./usage"

const captured: PRMergeRatePayload = {
  status: "no_prs",
  metric: "pr_outcomes_by_opening_invocation_configured_model",
  definition: "PR outcomes",
  maturity_days: 21,
  period: "30d",
  suppression_threshold: 5,
  cohorts: [],
  unavailable_thread_ids: [],
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
      <TooltipProvider>
        <UsageAnalytics
          period="30d"
          login="reader"
          isAdmin={false}
          onPeriodChange={() => {}}
        />
      </TooltipProvider>
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
  expect(screen.getByText("Analytics are updating")).toBeTruthy()
  fireEvent.click(screen.getByText("Details"))
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
        avg_pr_cost_usd: 2.5,
        prs_with_complete_cost: 5,
        merged: 3,
        closed_without_merge: 1,
        mature_pending: 1,
        waiting: 0,
        cohort_size: 5,
        decided_denominator: 4,
        decided_merge_rate: 0.75,
        mature_denominator: 5,
        mature_cohort_merge_share: 0.6,
        avg_merge_seconds: 172800,
        efforts: [
          {
            effort: "high",
            avg_pr_cost_usd: 2.5,
            prs_with_complete_cost: 5,
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
      },
    ],
  })
  await act(() => client.invalidateQueries())
  expect(await screen.findByText("example-model")).toBeTruthy()
  expect(screen.getByText("Analytics are up to date")).toBeTruthy()
  expect(
    screen.getByLabelText("Analytics coverage").querySelector("details")?.open
  ).toBe(true)
  expect(screen.getAllByText(/Last event processed/).length).toBeGreaterThan(0)
  client.clear()
})

it.each([
  [14, "hover"],
  [21, "focus"],
  [7, "tap"],
] as const)(
  "shows Open totals and age breakdown at %i days on %s",
  async (maturityDays, interaction) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
      ...captured,
      status: "ready",
      maturity_days: maturityDays,
      cohorts: [
        {
          model_id: "example-model",
          model_attribution_quality: "configured",
          avg_pr_cost_usd: 2.5,
          prs_with_complete_cost: 7,
          merged: 3,
          closed_without_merge: 1,
          mature_pending: 1,
          waiting: 2,
          cohort_size: 7,
          decided_denominator: 4,
          decided_merge_rate: 0.75,
          mature_denominator: 5,
          mature_cohort_merge_share: 0.6,
          avg_merge_seconds: 90000,
          efforts: [
            {
              effort: "high",
              avg_pr_cost_usd: 2.5,
              prs_with_complete_cost: 7,
              merged: 3,
              closed_without_merge: 1,
              mature_pending: 1,
              waiting: 2,
              cohort_size: 7,
              decided_denominator: 4,
              decided_merge_rate: 0.75,
              mature_denominator: 5,
              mature_cohort_merge_share: 0.6,
            },
          ],
        },
      ],
    })
    const client = mountReport()
    const row = (await screen.findByText("example-model")).closest("tr")!
    expect(
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent)
    ).toEqual([
      "example-modelHigh · configured attribution",
      "7",
      "3",
      "1",
      "3",
      "60%",
      "$2.50",
      "1d",
    ])

    const table = row.closest("table")!
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent)
    ).toEqual([
      "Opening model",
      "PRs opened",
      "Merged",
      "Closed without merge",
      "Open",
      "Merge rate",
      "Avg PR cost",
      "Avg time to merge",
    ])

    const openCount = within(row).getByRole("button", { name: "3" })
    if (interaction === "hover") {
      fireEvent.mouseEnter(openCount)
      fireEvent.mouseMove(openCount)
    } else if (interaction === "focus") {
      act(() => openCount.focus())
    } else {
      fireEvent.pointerDown(openCount, { pointerType: "touch" })
      fireEvent.pointerUp(openCount, { pointerType: "touch" })
      fireEvent.click(openCount)
    }
    const breakdown = (
      await screen.findByText(`2 open for less than ${maturityDays} days`)
    ).parentElement!
    expect(
      within(breakdown).getByText(`2 open for less than ${maturityDays} days`)
    ).toBeTruthy()
    expect(
      within(breakdown).getByText(`1 open for ${maturityDays} days or longer`)
    ).toBeTruthy()
    fireEvent.mouseLeave(openCount)
    act(() => openCount.blur())
    fireEvent.keyDown(openCount, { key: "Escape" })

    const mergeRate = within(table).getByText("Merge rate")
    act(() => mergeRate.focus())
    expect(
      await screen.findByText(/Includes merged and closed PRs/)
    ).toBeTruthy()

    const avgTime = within(row).getByRole("button", { name: "1d" })
    act(() => avgTime.focus())
    expect(await screen.findByText(/Based on 3 merged PRs/)).toBeTruthy()
    expect(row.textContent).not.toContain("Based on")
    act(() => avgTime.blur())
    fireEvent.keyDown(avgTime, { key: "Escape" })

    fireEvent.click(screen.getByText("How these numbers work"))
    expect(
      screen.getByText("Open", { selector: "strong" }).closest("p")?.textContent
    ).toContain("includes PRs that haven’t been merged or closed")
    expect(screen.queryByText(/resolved merge rate/)).toBeNull()
    expect(screen.getByText(/Merge rate = merged/)).toBeTruthy()
    expect(screen.queryByText(/Resolved merge rate = merged/)).toBeNull()
    client.clear()
  }
)

it("shows unavailable attribution thread IDs for admin triage", async () => {
  const threadId = "73b0906a-ff36-59c7-9cc5-ad622fff673e"
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    unavailable_thread_ids: [threadId],
  })
  const client = mountReport()
  fireEvent.click(await screen.findByText("Unavailable model attribution (1)"))
  expect(screen.getByText(threadId)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Copy" }))
  expect(writeText).toHaveBeenCalledWith(threadId)
  client.clear()
})

it("expands model totals into reasoning effort rows", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    status: "ready",
    cohorts: [
      {
        model_id: "example-model",
        model_attribution_quality: "configured",
        avg_pr_cost_usd: 2.5,
        prs_with_complete_cost: 4,
        avg_merge_seconds: 172800,
        merged: 3,
        closed_without_merge: 1,
        mature_pending: 0,
        waiting: 0,
        cohort_size: 4,
        decided_denominator: 4,
        decided_merge_rate: 0.75,
        mature_denominator: 4,
        mature_cohort_merge_share: 0.75,
        efforts: [
          {
            effort: "low",
            avg_pr_cost_usd: 2.5,
            prs_with_complete_cost: 2,
            merged: 1,
            closed_without_merge: 1,
            mature_pending: 0,
            waiting: 0,
            cohort_size: 2,
            decided_denominator: 2,
            decided_merge_rate: 0.5,
            mature_denominator: 2,
            mature_cohort_merge_share: 0.5,
          },
          {
            effort: "high",
            avg_pr_cost_usd: 2.5,
            prs_with_complete_cost: 2,
            merged: 2,
            closed_without_merge: 0,
            mature_pending: 0,
            waiting: 0,
            cohort_size: 2,
            decided_denominator: 2,
            decided_merge_rate: 1,
            mature_denominator: 2,
            mature_cohort_merge_share: 1,
          },
        ],
      },
    ],
  })
  const client = mountReport()
  expect(await screen.findByText(/All efforts/)).toBeTruthy()
  expect(screen.queryByText("Low")).toBeNull()
  fireEvent.click(
    screen.getByRole("button", { name: /Expand.*reasoning efforts/ })
  )
  expect(screen.getByText("Low")).toBeTruthy()
  expect(screen.getByText("High")).toBeTruthy()
  client.clear()
})

it("shows an em dash for avg time to merge when a group has no merges", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    status: "ready",
    cohorts: [
      {
        model_id: "example-model",
        model_attribution_quality: "configured",
        avg_pr_cost_usd: null,
        prs_with_complete_cost: 0,
        merged: 0,
        closed_without_merge: 2,
        mature_pending: 0,
        waiting: 0,
        cohort_size: 2,
        decided_denominator: 2,
        decided_merge_rate: 0,
        mature_denominator: 2,
        mature_cohort_merge_share: 0,
        avg_merge_seconds: null,
        efforts: [],
      },
    ],
  })
  const client = mountReport()
  const row = (await screen.findByText("example-model")).closest("tr")!
  const cells = within(row).getAllByRole("cell")
  expect(cells.at(-1)?.textContent).toBe("\u2014")
  expect(within(row).queryByRole("button", { name: /Based on/ })).toBeNull()
  expect(row.textContent).not.toContain("Based on")
  client.clear()
})

it("shortens model paths across usage tables", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    status: "ready",
    cohorts: [
      {
        model_id: "fireworks:accounts/fireworks/models/glm-5p3-flash",
        model_attribution_quality: "configured",
        avg_pr_cost_usd: 2.5,
        prs_with_complete_cost: 5,
        merged: 1,
        closed_without_merge: 0,
        mature_pending: 0,
        waiting: 0,
        cohort_size: 1,
        decided_denominator: 1,
        decided_merge_rate: 1,
        mature_denominator: 1,
        mature_cohort_merge_share: 1,
        avg_merge_seconds: 3600,
        efforts: [
          {
            effort: "medium",
            avg_pr_cost_usd: 2.5,
            prs_with_complete_cost: 5,
            merged: 1,
            closed_without_merge: 0,
            mature_pending: 0,
            waiting: 0,
            cohort_size: 1,
            decided_denominator: 1,
            decided_merge_rate: 1,
            mature_denominator: 1,
            mature_cohort_merge_share: 1,
          },
        ],
      },
    ],
  })
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      {
        rank: 1,
        user: { name: "Model Reader", github_login: "reader", email: null },
        favorite_model: "fireworks:accounts/fireworks/models/glm-5p3-flash",
        invocations: 1,
        prs_opened: 1,
        merged_prs: 1,
        agent_loc: 1,
        additions: 1,
        deletions: 0,
        total_tokens: 1,
        total_cost_usd: 0,
        invocations_without_cost: 0,
        invocations_with_partial_cost: 0,
        avg_invocation_seconds: 1,
      },
    ],
  })

  const client = mountReport()
  expect(await screen.findAllByText("glm-5p3-flash")).toHaveLength(2)
  expect(screen.queryByText(/accounts\/fireworks\/models/)).toBeNull()
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
  expect(
    screen.queryByRole("status", { name: "Analytics coverage" })
  ).toBeTruthy()

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
  expect(screen.getByText("Analytics need attention")).toBeTruthy()
  expect(
    screen.getAllByText(/Some events could not be processed/).length
  ).toBeGreaterThan(0)
  expect(screen.queryByRole("table")).toBeNull()
  expect(screen.queryByText(/No PRs have been recorded/)).toBeNull()
  client.clear()
})

it("resets leaderboard pagination when the period changes outside the selector", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockImplementation(
    async (period, _limit, cursor) => ({
      ...emptyUsage,
      period: period ?? "30d",
      total_members: 11,
      next_cursor: cursor ? null : "next-page",
      rows: [{ ...costRow, rank: cursor ? 11 : 1 }],
    })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageAnalytics
          period="30d"
          login="reader"
          isAdmin={false}
          onPeriodChange={() => {}}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByRole("button", { name: "Next" }))
  expect(await screen.findByText("Page 2 of 2")).toBeTruthy()

  view.rerender(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageAnalytics
          period="7d"
          login="reader"
          isAdmin={false}
          onPeriodChange={() => {}}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(await screen.findByText("Page 1 of 2")).toBeTruthy()
  expect(api.usageLeaderboard).toHaveBeenLastCalledWith("7d", 10, undefined)
  client.clear()
})

it("switches the usage count and average duration to threads", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      {
        ...costRow,
        invocations: 4,
        threads: 2,
        avg_invocation_seconds: 30,
        avg_thread_seconds: 90,
      },
    ],
  })
  const client = mountReport()
  expect(
    await screen.findByRole("columnheader", { name: "Invocations" })
  ).toBeTruthy()
  expect(
    screen.getByRole("columnheader", { name: "Avg Invocation Duration" })
  ).toBeTruthy()

  fireEvent.click(screen.getByRole("button", { name: "threads" }))

  expect(screen.getByRole("columnheader", { name: "Threads" })).toBeTruthy()
  expect(
    screen.getByRole("columnheader", { name: "Avg Thread Duration" })
  ).toBeTruthy()
  const row = screen.getByText("Cost Reader").closest("tr")!
  expect(within(row).getByText("2")).toBeTruthy()
  expect(within(row).getByText("2m")).toBeTruthy()
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
  expect(screen.getByLabelText("Analytics coverage")).toBeTruthy()
  expect(screen.getByText("Analytics are updating")).toBeTruthy()
  fireEvent.click(screen.getByText("Details"))
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
        invocations_without_cost: 0,
        invocations_with_partial_cost: 0,
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

it("hides a GitHub login when it duplicates the user name", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      {
        rank: 1,
        user: { name: "reader", github_login: "reader", email: null },
        favorite_model: "configured-model",
        invocations: 1,
        prs_opened: 0,
        merged_prs: 0,
        agent_loc: 0,
        additions: 0,
        deletions: 0,
        total_tokens: 100,
        total_cost_usd: 0,
        invocations_without_cost: 1,
        invocations_with_partial_cost: 0,
        avg_invocation_seconds: 30,
      },
    ],
  })
  const client = mountReport()
  expect(await screen.findAllByText("reader")).toHaveLength(1)
  client.clear()
})

it("shows the GitHub username and marks the current user", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    current_user_rank: 1,
    rows: [
      {
        ...costRow,
        user: {
          name: "Mason Daugherty",
          github_login: "mdrxy",
          email: "mason@example.com",
        },
      },
    ],
  })
  const client = mountReport()
  const row = (await screen.findByText("Mason Daugherty")).closest("tr")!
  expect(within(row).getByText("mdrxy")).toBeTruthy()
  expect(within(row).getByText("You")).toBeTruthy()
  expect(within(row).queryByText("mason@example.com")).toBeNull()
  client.clear()
})

it("links the user name and avatar to their GitHub profile only when a login exists", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 2,
    rows: [
      { ...costRow, rank: 1 },
      {
        ...costRow,
        rank: 2,
        user: {
          name: "Anonymous Reader",
          github_login: null,
          email: "r@example.com",
        },
      },
    ],
  })
  const client = mountReport()
  const linked = await screen.findByRole("link", { name: "Cost Reader" })
  expect(linked.getAttribute("href")).toBe("https://github.com/reader")
  expect(linked.getAttribute("target")).toBe("_blank")
  expect(linked.getAttribute("rel")).toBe("noreferrer")
  expect(screen.queryByRole("link", { name: "Anonymous Reader" })).toBeNull()
  const avatarLink = screen.getByText("CR").closest("a")
  expect(avatarLink?.getAttribute("href")).toBe("https://github.com/reader")
  expect(avatarLink?.getAttribute("target")).toBe("_blank")
  expect(avatarLink?.getAttribute("rel")).toBe("noreferrer")
  expect(screen.getByText("AR").closest("a")).toBeNull()
  client.clear()
})

const costRow: UsageLeaderboardRow = {
  rank: 1,
  user: { name: "Cost Reader", github_login: "reader", email: null },
  favorite_model: "example-model",
  invocations: 2,
  prs_opened: 0,
  merged_prs: 0,
  agent_loc: 0,
  additions: 0,
  deletions: 0,
  total_tokens: 100,
  total_cost_usd: 0,
  invocations_without_cost: 0,
  invocations_with_partial_cost: 0,
  avg_invocation_seconds: 90,
}

it.each([
  {
    name: "confirmed zero",
    cost: 0,
    missing: 0,
    partial: 0,
    amount: "$0.00",
    label: null,
  },
  {
    name: "complete cost",
    cost: 2.5,
    missing: 0,
    partial: 0,
    amount: "$2.50",
    label: null,
  },
  {
    name: "all missing",
    cost: 0,
    missing: 2,
    partial: 0,
    amount: "—",
    label: "Unavailable",
  },
  {
    name: "mixed zero and missing",
    cost: 0,
    missing: 1,
    partial: 0,
    amount: "$0.00",
    label: "Incomplete",
  },
  {
    name: "partial cost",
    cost: 2.5,
    missing: 0,
    partial: 1,
    amount: "$2.50",
    label: "Incomplete",
  },
  {
    name: "unknown zero coverage",
    cost: 0,
    missing: undefined,
    partial: undefined,
    amount: "—",
    label: "Unavailable",
  },
  {
    name: "unknown positive coverage",
    cost: 2.5,
    missing: undefined,
    partial: undefined,
    amount: "$2.50",
    label: "Incomplete",
  },
])(
  "distinguishes $name in the cost column",
  async ({ cost, missing, partial, amount, label }) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
    vi.mocked(api.usageLeaderboard).mockResolvedValue({
      ...emptyUsage,
      total_members: 1,
      rows: [
        {
          ...costRow,
          total_cost_usd: cost,
          invocations_without_cost: missing,
          invocations_with_partial_cost: partial,
        },
      ],
    })
    const client = mountReport()
    const row = (await screen.findByText("Cost Reader")).closest("tr")!
    expect(within(row).getByText(amount)).toBeTruthy()
    const indicator = within(row).queryByRole("button", {
      name: /Cost (unavailable|incomplete)/,
    })
    expect(indicator?.textContent ?? null).toBe(label)
    client.clear()
  }
)

it("explains incomplete coverage on focus and removes the indicator when costs recover", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(captured)
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      {
        ...costRow,
        total_cost_usd: 2.5,
        invocations_without_cost: 1,
        invocations_with_partial_cost: 1,
      },
    ],
  })
  const client = mountReport()
  const trigger = await screen.findByRole("button", { name: "Cost incomplete" })
  act(() => trigger.focus())
  const tooltip = await screen.findByText(/Recorded cost so far/)
  expect(tooltip.textContent).toContain(
    "Costs are missing for 1 of 2 invocations (50%)."
  )
  expect(tooltip.textContent).toContain(
    "Costs are partial for 1 of 2 invocations."
  )

  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [{ ...costRow, total_cost_usd: 3.75 }],
  })
  await act(() => client.invalidateQueries({ queryKey: ["usageLeaderboard"] }))
  expect(await screen.findByText("$3.75")).toBeTruthy()
  expect(screen.queryByRole("button", { name: "Cost incomplete" })).toBeNull()
  client.clear()
})

it("explains omitted PR costs in the average", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
    ...captured,
    status: "ready",
    cohorts: [
      {
        model_id: "cost-model",
        model_attribution_quality: "configured",
        avg_merge_seconds: 3600,
        merged: 1,
        closed_without_merge: 0,
        mature_pending: 0,
        waiting: 2,
        cohort_size: 3,
        decided_denominator: 1,
        decided_merge_rate: 1,
        mature_denominator: 1,
        mature_cohort_merge_share: 1,
        avg_pr_cost_usd: 2.5,
        prs_with_complete_cost: 2,
        efforts: [],
      },
    ],
  })
  const client = mountReport()
  const warning = await screen.findByRole("button", {
    name: "Average PR cost incomplete",
  })
  act(() => warning.focus())
  expect(await screen.findByText(/1 of 3 PRs is omitted/)).toBeTruthy()
  client.clear()
})

it.each([
  [0, 2, 2, "$0.00"],
  [12.345, 2, 2, "$12.35"],
  [2.5, 2, 3, "$2.50"],
  [null, 1, 2, "—"],
  [null, 0, 2, "—"],
] as const)(
  "renders PR average %s with coverage %s of %s",
  async (cost, covered, cohortSize, amount) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue({
      ...captured,
      status: "ready",
      cohorts: [
        {
          model_id: "cost-model",
          model_attribution_quality: "configured",
          merged: 1,
          closed_without_merge: 0,
          mature_pending: 0,
          waiting: cohortSize - 1,
          cohort_size: cohortSize,
          decided_denominator: 1,
          decided_merge_rate: 1,
          mature_denominator: 1,
          mature_cohort_merge_share: 1,
          avg_merge_seconds: 3600,
          avg_pr_cost_usd: cost,
          prs_with_complete_cost: covered,
          efforts: [
            {
              effort: "medium",
              avg_pr_cost_usd: cost,
              prs_with_complete_cost: covered,
              merged: 1,
              closed_without_merge: 0,
              mature_pending: 0,
              waiting: cohortSize - 1,
              cohort_size: cohortSize,
              decided_denominator: 1,
              decided_merge_rate: 1,
              mature_denominator: 1,
              mature_cohort_merge_share: 1,
            },
          ],
        },
      ],
    })
    const client = mountReport()
    const row = (await screen.findByText("cost-model")).closest("tr")!
    expect(within(row).getByText(amount)).toBeTruthy()
    expect(
      within(row).queryByRole("button", {
        name: "Average PR cost incomplete",
      }) !== null
    ).toBe(covered < cohortSize)
    fireEvent.click(screen.getByText("How these numbers work"))
    expect(
      screen.getByText(/Missing or partial costs are not zero/)
    ).toBeTruthy()
    client.clear()
  }
)
