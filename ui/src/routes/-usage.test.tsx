/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type PRMergeRateCohort,
  type PRMergeRatePayload,
  type PRMergeRateResponse,
  type UsageLeaderboardPayload,
  type UsageLeaderboardRow,
} from "@/lib/api"
import { TooltipProvider } from "@/components/ui/tooltip"
import { makeQueryClient } from "@/lib/query"

import { UsageAnalytics, UsageDateRange } from "./usage"

const FETCHED_AT = "2026-09-11T12:01:30Z"

function report(payload: PRMergeRatePayload): PRMergeRateResponse {
  return { payload, fetchedAt: FETCHED_AT }
}

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
    invocations: 0,
    total_cost_usd: 0,
    avg_invocation_cost_usd: null,
    invocations_without_cost: 0,
    invocations_with_partial_cost: 0,
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

function mountReport(onPeriodChange = (_period: string) => {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageDateRange period="30d" onPeriodChange={onPeriodChange} />
        <UsageAnalytics period="30d" login="reader" isAdmin={false} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  return client
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("labels the shared date range and changes it independently of usage scope", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
  const onPeriodChange = vi.fn()
  mountReport(onPeriodChange)
  const range = screen.getByRole("combobox", { name: "Date range" })
  const outcomes = screen.getByText("PR outcomes", { selector: "h2" })
  expect(
    range.compareDocumentPosition(outcomes) & Node.DOCUMENT_POSITION_FOLLOWING
  ).toBeTruthy()
  expect(
    screen.getByRole("group", { name: "Usage scope" }).contains(range)
  ).toBe(false)
  fireEvent.click(range)
  const option = await screen.findByRole("option", { name: "Last 24h" })
  fireEvent.keyDown(option, { key: "Enter" })
  expect(onPeriodChange).toHaveBeenCalledWith("24h")
  fireEvent.click(screen.getByRole("button", { name: "threads" }))
  expect(onPeriodChange).toHaveBeenCalledTimes(1)
})

it("shows delivery lag separately from suppression, then refreshes to a populated report", async () => {
  const query = vi
    .spyOn(api, "prMergeRateByModel")
    .mockResolvedValue(report(captured))
  const client = mountReport()
  expect(await screen.findByText(/No PRs have been recorded/)).toBeTruthy()
  expect(screen.getByText("Analytics are updating")).toBeTruthy()
  fireEvent.click(screen.getByText("Details"))
  expect(
    screen.getAllByText(/still waiting to be processed/).length
  ).toBeGreaterThan(0)
  expect(screen.getByText(/Reporting since/)).toBeTruthy()
  expect(
    screen
      .getByText(new Date(captured.reporting_cutover_at).toLocaleString())
      .getAttribute("datetime")
  ).toBe(captured.reporting_cutover_at)
  expect(screen.queryByText(/too small to show/)).toBeNull()

  query.mockResolvedValue(
    report({
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
          avg_merge_seconds: 172800,
          avg_delivery_seconds: 7200,
          efforts: [
            {
              effort: "high",
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
          median_distance_basis_points: 1750,
          distance_sample_size: 3,
        },
      ],
    })
  )
  await act(() => client.invalidateQueries())
  expect(await screen.findByText("example-model")).toBeTruthy()
  expect(screen.getByText("Analytics are up to date")).toBeTruthy()
  expect(
    screen.getByLabelText("Analytics coverage").querySelector("details")?.open
  ).toBe(true)
  expect(screen.getAllByText(/Last event processed/).length).toBe(1)
  client.clear()
})

it.each([0, 1, 4, 5])(
  "flags only small nonempty distance samples without inline counts (%i measured)",
  async (samples) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
      report({
        ...captured,
        status: "ready",
        cohorts: [
          {
            model_id: "sample-model",
            model_attribution_quality: "configured",
            merged: 8,
            closed_without_merge: 0,
            mature_pending: 0,
            waiting: 0,
            cohort_size: 8,
            decided_denominator: 8,
            decided_merge_rate: 1,
            mature_denominator: 8,
            mature_cohort_merge_share: 1,
            efforts: [],
            median_distance_basis_points: samples ? 1750 : null,
            distance_sample_size: samples,
            avg_merge_seconds: null,
            avg_delivery_seconds: null,
          },
        ],
      })
    )
    const client = mountReport()
    const row = (await screen.findByText("sample-model")).closest("tr")!
    expect(
      within(row).getByRole("button", { name: samples ? "17.5%" : "—" })
    ).toBeTruthy()
    expect(within(row).queryByText(/measured \/ .* merged/)).toBeNull()
    expect(within(row).queryByText("Small sample") !== null).toBe(
      samples > 0 && samples < 5
    )
    client.clear()
  }
)

it("sorts PR outcomes before pagination and toggles column direction", async () => {
  const cohort = (model: string, size: number): PRMergeRateCohort => ({
    model_id: model,
    model_attribution_quality: "configured",
    merged: size,
    closed_without_merge: 0,
    mature_pending: 0,
    waiting: 0,
    cohort_size: size,
    decided_denominator: size,
    decided_merge_rate: 1,
    mature_denominator: size,
    mature_cohort_merge_share: 1,
    avg_merge_seconds: size,
    avg_delivery_seconds: size,
    efforts: [],
  })
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "ready",
      cohorts: [
        cohort("z-model", 20),
        cohort("a-model", 1),
        ...Array.from({ length: 9 }, (_, index) =>
          cohort(`m-${index}`, index + 2)
        ),
      ],
    })
  )
  const client = mountReport()
  expect(await screen.findByText("z-model")).toBeTruthy()
  expect(screen.queryByText("a-model")).toBeNull()

  fireEvent.click(screen.getByRole("button", { name: "Opening model" }))
  expect(await screen.findByText("a-model")).toBeTruthy()
  expect(screen.queryByText("z-model")).toBeNull()
  expect(
    screen
      .getByRole("columnheader", { name: "Opening model" })
      .getAttribute("aria-sort")
  ).toBe("ascending")

  fireEvent.click(screen.getByRole("button", { name: "Opening model" }))
  expect(await screen.findByText("z-model")).toBeTruthy()
  expect(
    screen
      .getByRole("columnheader", { name: "Opening model" })
      .getAttribute("aria-sort")
  ).toBe("descending")
  client.clear()
})

it.each([
  [14, "hover"],
  [21, "focus"],
  [7, "tap"],
] as const)(
  "shows Open totals and age breakdown at %i days on %s",
  async (maturityDays, interaction) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
      report({
        ...captured,
        status: "ready",
        maturity_days: maturityDays,
        cohorts: [
          {
            model_id: "example-model",
            model_attribution_quality: "configured",
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
            avg_delivery_seconds: 7200,
            efforts: [
              {
                effort: "high",
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
            median_distance_basis_points: 1750,
            distance_sample_size: 3,
          },
        ],
      })
    )
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
      "17.5%Small sample",
      "60%",
      "2h",
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
      "Median distance",
      "Merge rate",
      "Avg time to PR",
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

    expect(within(row).queryByRole("button", { name: "1d" })).toBeNull()
    const avgTime = within(table).getByText("Avg time to merge")
    act(() => avgTime.focus())
    expect(await screen.findByText("Unmerged PRs are excluded.")).toBeTruthy()
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
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      unavailable_thread_ids: [threadId],
    })
  )
  const client = mountReport()
  fireEvent.click(await screen.findByText("Unavailable model attribution (1)"))
  expect(screen.getByText(threadId)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Copy" }))
  expect(writeText).toHaveBeenCalledWith(threadId)
  client.clear()
})

it("expands model totals into reasoning effort rows", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "ready",
      cohorts: [
        {
          model_id: "example-model",
          model_attribution_quality: "configured",
          avg_merge_seconds: 172800,
          avg_delivery_seconds: 5400,
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
  )
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
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "ready",
      cohorts: [
        {
          model_id: "example-model",
          model_attribution_quality: "configured",
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
          avg_delivery_seconds: null,
          efforts: [],
        },
      ],
    })
  )
  const client = mountReport()
  const row = (await screen.findByText("example-model")).closest("tr")!
  const cells = within(row).getAllByRole("cell")
  expect(cells.at(-1)?.textContent).toBe("\u2014")
  expect(within(row).queryByRole("button", { name: /Based on/ })).toBeNull()
  expect(row.textContent).not.toContain("Based on")
  client.clear()
})

it.each([
  {
    name: "unsupported on an older backend when the key is omitted",
    withKey: false,
    expected: "Metric unavailable from this backend",
  },
  {
    name: "empty when no PR has valid timing (null)",
    withKey: true,
    expected: "No PRs with valid timing in this group",
  },
])("marks avg time to PR $name", async ({ withKey, expected }) => {
  const cohort = {
    model_id: "example-model",
    model_attribution_quality: "configured",
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
    efforts: [] as PRMergeRateCohort["efforts"],
    ...(withKey ? { avg_delivery_seconds: null } : {}),
  } as PRMergeRateCohort
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({ ...captured, status: "ready", cohorts: [cohort] })
  )
  const client = mountReport()
  const row = (await screen.findByText("example-model")).closest("tr")!
  const deliveryCell = within(row).getAllByRole("cell").at(-2)!
  expect(deliveryCell.textContent).toBe("\u2014")
  expect(within(deliveryCell).getByTitle(expected)).toBeTruthy()
  client.clear()
})

it("renders a zero avg time to PR as a real duration, not an empty marker", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "ready",
      cohorts: [
        {
          model_id: "example-model",
          model_attribution_quality: "configured",
          merged: 1,
          closed_without_merge: 0,
          mature_pending: 0,
          waiting: 0,
          cohort_size: 1,
          decided_denominator: 1,
          decided_merge_rate: 1,
          mature_denominator: 1,
          mature_cohort_merge_share: 1,
          avg_merge_seconds: 0,
          avg_delivery_seconds: 0,
          efforts: [],
        },
      ],
    })
  )
  const client = mountReport()
  const row = (await screen.findByText("example-model")).closest("tr")!
  const deliveryCell = within(row).getAllByRole("cell").at(-2)!
  expect(deliveryCell.textContent).not.toBe("\u2014")
  expect(deliveryCell.textContent).toContain("0")
  expect(
    within(deliveryCell).queryByTitle(/valid timing|unavailable/i)
  ).toBeNull()
  client.clear()
})

it("shortens model paths while preserving providers across usage tables", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "ready",
      cohorts: [
        {
          model_id: "fireworks:accounts/fireworks/models/glm-5p3-flash",
          model_attribution_quality: "configured",
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
          avg_delivery_seconds: 1800,
          efforts: [
            {
              effort: "medium",
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
  )
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      {
        rank: 1,
        user: { name: "Model Reader", github_login: "reader", email: null },
        favorite_model: "fireworks:accounts/fireworks/models/glm-5p3-flash",
        favorite_model_effort: "high",
        invocations: 1,
        prs_opened: 1,
        merged_prs: 1,
        agent_loc: 1,
        feedback_given: 0,
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
  expect(await screen.findAllByText("fireworks:glm-5p3-flash")).toHaveLength(2)
  expect(screen.getByText("high")).toBeTruthy()
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

  query.mockResolvedValue(
    report({
      ...captured,
      status: "not_started",
      collection_started_at: null,
      completeness: "not_started",
      has_pending_events: false,
    })
  )
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))
  expect(
    await screen.findByText(
      /No analytics records have been captured since the reporting cutover/
    )
  ).toBeTruthy()
  expect(screen.queryByRole("alert")).toBeNull()
  client.clear()
})

it("refreshes the usage leaderboard and merge rate report from the coverage footer", async () => {
  let pendingReport: () => void = () => {}
  vi.spyOn(api, "prMergeRateByModel")
    .mockResolvedValueOnce(report(captured))
    .mockImplementationOnce(
      () =>
        new Promise<PRMergeRateResponse>((resolve) => {
          pendingReport = () => resolve(report(captured))
        })
    )
    .mockResolvedValue(report(captured))
  const client = mountReport()
  expect(await screen.findByText("Analytics are updating")).toBeTruthy()

  const beforeUsage = vi.mocked(api.usageLeaderboard).mock.calls.length
  const beforeReport = vi.mocked(api.prMergeRateByModel).mock.calls.length
  const button = screen.getByRole("button", { name: "Refresh now" })
  fireEvent.click(button)
  expect(vi.mocked(api.usageLeaderboard).mock.calls.length).toBe(
    beforeUsage + 1
  )
  expect(vi.mocked(api.prMergeRateByModel).mock.calls.length).toBe(
    beforeReport + 1
  )

  const refreshing = await screen.findByRole("button", {
    name: "Refreshing…",
  })
  expect(refreshing).toHaveProperty("disabled", true)
  fireEvent.click(refreshing)
  expect(vi.mocked(api.prMergeRateByModel).mock.calls.length).toBe(
    beforeReport + 1
  )

  await act(() => pendingReport())
  expect(
    await screen.findByRole("button", { name: "Refresh now" })
  ).toBeTruthy()
  expect(
    screen.getByLabelText("Analytics coverage").querySelector("details")?.open
  ).toBe(false)
  client.clear()
})

it("announces a failed refresh while keeping the last good PR report", async () => {
  const query = vi
    .spyOn(api, "prMergeRateByModel")
    .mockResolvedValue(report(captured))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageAnalytics period="30d" login="reader" isAdmin={false} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(await screen.findByText(/No PRs have been recorded/)).toBeTruthy()

  fireEvent.click(screen.getByText("Details"))
  query.mockRejectedValue(new ApiError(503, "unavailable"))
  fireEvent.click(screen.getByRole("button", { name: "Refresh now" }))
  expect(await screen.findByText(/Last PR report refresh failed/)).toBeTruthy()
  expect(screen.getByText(/HTTP 503/)).toBeTruthy()
  // The retained report and its last successful fetch timestamp stay on screen.
  expect(screen.getByText(/No PRs have been recorded/)).toBeTruthy()
  expect(screen.getByText(/last fetched by this browser:/)).toBeTruthy()
  expect(
    screen.getByText(/last fetched by this browser:/).textContent
  ).toContain(new Date(FETCHED_AT).toLocaleString())

  query.mockResolvedValue(report(captured))
  fireEvent.click(screen.getByRole("button", { name: "Refresh now" }))
  await waitFor(() =>
    expect(screen.queryByText(/Last PR report refresh failed/)).toBeNull()
  )
  client.clear()
})

it("clears a failed refresh announcement when an automatic refetch succeeds", async () => {
  const query = vi
    .spyOn(api, "prMergeRateByModel")
    .mockResolvedValue(report(captured))
  const client = makeQueryClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageAnalytics period="30d" login="reader" isAdmin={false} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(await screen.findByText(/No PRs have been recorded/)).toBeTruthy()

  fireEvent.click(screen.getByText("Details"))
  query.mockRejectedValue(new ApiError(503, "unavailable"))
  fireEvent.click(screen.getByRole("button", { name: "Refresh now" }))
  expect(await screen.findByText(/Last PR report refresh failed/)).toBeTruthy()

  // A success the user did not trigger (interval/focus refetch) also clears it.
  query.mockResolvedValue(report(captured))
  await act(() => client.refetchQueries({ queryKey: ["prMergeRateByModel"] }))
  await waitFor(() =>
    expect(screen.queryByText(/Last PR report refresh failed/)).toBeNull()
  )
  client.clear()
})

it("keeps failed delivery visible when all PR groups are suppressed", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({
      ...captured,
      status: "suppressed",
      has_pending_events: false,
      has_failed_events: true,
    })
  )
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
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
        <UsageAnalytics period="30d" login="reader" isAdmin={false} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByRole("button", { name: "Next" }))
  expect(await screen.findByText("Page 2 of 2")).toBeTruthy()

  view.rerender(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <UsageAnalytics period="7d" login="reader" isAdmin={false} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(await screen.findByText("Page 1 of 2")).toBeTruthy()
  expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
    "7d",
    10,
    undefined,
    "rank",
    "asc"
  )
  client.clear()
})

it("switches the usage count and average duration to threads", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
  expect(
    screen.getAllByText(/still waiting to be processed/).length
  ).toBeGreaterThan(0)
  expect(screen.getByText("Reviewed PRs")).toBeTruthy()
  expect(
    screen.queryByText("Usage analytics is unavailable on this deployment.")
  ).toBeNull()
  client.clear()
})

it("shows usage metrics but removes stale results when a refresh becomes unavailable", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
        merged_prs_per_thread: 0.25,
        agent_loc: 35,
        feedback_given: 0,
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
  expect(screen.getByText("0.25")).toBeTruthy()
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

it.each([
  {
    runs: 3,
    missing: 1,
    partial: 1,
    cost: 2.75,
    average: 2,
    total: "$2.75",
    mean: "$2.00",
  },
  {
    runs: 1,
    missing: 1,
    partial: 0,
    cost: 0,
    average: null,
    total: "—",
    mean: "—",
  },
  {
    runs: 1,
    missing: 0,
    partial: 0,
    cost: 0,
    average: 0,
    total: "$0.00",
    mean: "$0.00",
  },
])(
  "reports review cost coverage without treating missing costs as free: $missing missing",
  async ({ runs, missing, partial, cost, average, total, mean }) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
    vi.mocked(api.usageLeaderboard).mockResolvedValue({
      ...emptyUsage,
      reviewer_stats: {
        ...emptyUsage.reviewer_stats,
        invocations: runs,
        total_cost_usd: cost,
        avg_invocation_cost_usd: average,
        invocations_without_cost: missing,
        invocations_with_partial_cost: partial,
      },
    })
    const client = mountReport()
    const totalCard = (await screen.findByText("Review LLM cost"))
      .parentElement!
    expect(within(totalCard).getByText(total)).toBeTruthy()
    const averageCard = screen.getByText(
      "Average per review run"
    ).parentElement!
    expect(within(averageCard).getByText(mean)).toBeTruthy()
    expect(
      screen.getByText(
        missing || partial
          ? `${missing} missing · ${partial} partial`
          : "Recorded model cost"
      )
    ).toBeTruthy()
    expect(screen.getByText(/older reviews are not backfilled/)).toBeTruthy()
    client.clear()
  }
)

it("hides a GitHub login when it duplicates the user name", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
        feedback_given: 0,
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
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
  feedback_given: 0,
  additions: 0,
  deletions: 0,
  total_tokens: 100,
  total_cost_usd: 0,
  invocations_without_cost: 0,
  invocations_with_partial_cost: 0,
  avg_invocation_seconds: 90,
}

it("explains the feedback trophy on focus", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
  vi.mocked(api.usageLeaderboard).mockResolvedValue({
    ...emptyUsage,
    total_members: 1,
    rows: [
      { ...costRow, feedback_given: 3, is_top_feedback_contributor: true },
    ],
  })
  const client = mountReport()
  const trigger = await screen.findByRole("button", {
    name: "Top feedback contributor",
  })
  act(() => trigger.focus())
  expect(
    await screen.findByText("Most feedback given in the selected date range.")
  ).toBeTruthy()
  client.clear()
})

it.each([true, false, undefined])(
  "shows the feedback trophy only for a global leader (%s)",
  async (isTopContributor) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
    vi.mocked(api.usageLeaderboard).mockResolvedValue({
      ...emptyUsage,
      total_members: 20,
      rows: [
        {
          ...costRow,
          feedback_given: 3,
          is_top_feedback_contributor: isTopContributor,
        },
      ],
    })
    const client = mountReport()
    await screen.findByText("Cost Reader")
    const trophy = screen.queryByRole("button", {
      name: "Top feedback contributor",
    })
    expect(Boolean(trophy)).toBe(Boolean(isTopContributor))
    client.clear()
  }
)

it.each([
  [5, 2, "2.5"],
  [0, 0, "—"],
  [5, undefined, "—"],
])(
  "shows average invocations per thread for %s invocations and %s threads",
  async (invocations, threads, expected) => {
    vi.mocked(api.usageLeaderboard).mockResolvedValue({
      ...emptyUsage,
      total_members: 1,
      rows: [{ ...costRow, invocations, threads }],
    })
    const client = mountReport()
    const header = await screen.findByRole("columnheader", {
      name: "Avg Invocations / Thread",
    })
    const table = header.closest("table")!
    expect(within(table).getAllByRole("row")[1]?.children[4]?.textContent).toBe(
      expected
    )
    fireEvent.click(within(header).getByRole("button"))
    await waitFor(() =>
      expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
        "30d",
        10,
        undefined,
        "avg_invocations_per_thread",
        "desc"
      )
    )
    fireEvent.click(screen.getByRole("button", { name: "threads" }))
    expect(within(table).getAllByRole("row")[1]?.children[4]?.textContent).toBe(
      expected
    )
    client.clear()
  }
)

it.each([
  ["Invocations", "Threads", "invocations", "threads"],
  [
    "Avg Invocation Duration",
    "Avg Thread Duration",
    "avg_invocation_seconds",
    "avg_thread_seconds",
  ],
] as const)(
  "keeps sorting the visible %s metric when switching scopes",
  async (invocationLabel, threadLabel, invocationSort, threadSort) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
    vi.mocked(api.usageLeaderboard).mockImplementation(
      async (_period, _limit, cursor) => ({
        ...emptyUsage,
        total_members: 11,
        next_cursor: cursor ? null : "next-page",
        rows: [costRow],
      })
    )
    const client = mountReport()
    fireEvent.click(
      await screen.findByRole("button", { name: invocationLabel })
    )
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Next" }).disabled
      ).toBe(false)
    )
    fireEvent.click(await screen.findByRole("button", { name: "Next" }))
    expect(await screen.findByText("Page 2 of 2")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "threads" }))
    await waitFor(() =>
      expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
        "30d",
        10,
        undefined,
        threadSort,
        "desc"
      )
    )
    expect(await screen.findByText("Page 1 of 2")).toBeTruthy()
    expect(
      (
        await screen.findByRole("columnheader", { name: threadLabel })
      ).getAttribute("aria-sort")
    ).toBe("descending")

    fireEvent.click(screen.getByRole("button", { name: "invocations" }))
    await waitFor(() =>
      expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
        "30d",
        10,
        undefined,
        invocationSort,
        "desc"
      )
    )
    expect(
      (
        await screen.findByRole("columnheader", { name: invocationLabel })
      ).getAttribute("aria-sort")
    ).toBe("descending")
    client.clear()
  }
)

it("keeps sort controls focused while loading and prevents using a stale page cursor", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
  const initial: UsageLeaderboardPayload = {
    ...emptyUsage,
    total_members: 11,
    next_cursor: "rank-cursor",
    rows: [costRow],
  }
  let resolveSorted!: (value: UsageLeaderboardPayload) => void
  const sorted = new Promise<UsageLeaderboardPayload>((resolve) => {
    resolveSorted = resolve
  })
  vi.mocked(api.usageLeaderboard)
    .mockResolvedValueOnce(initial)
    .mockReturnValue(sorted)
  const client = mountReport()
  const header = await screen.findByRole("button", {
    name: "Invocations",
  })
  act(() => header.focus())
  fireEvent.click(header)
  await waitFor(() =>
    expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
      "30d",
      10,
      undefined,
      "invocations",
      "desc"
    )
  )
  expect(document.activeElement).toBe(header)
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "Next" }).disabled
  ).toBe(true)

  await act(async () =>
    resolveSorted({
      ...initial,
      next_cursor: "invocations-cursor",
      rows: [
        {
          ...costRow,
          rank: 11,
          user: { ...costRow.user, name: "Sorted Reader" },
        },
      ],
    })
  )
  const row = (await screen.findByText("Sorted Reader")).closest("tr")!
  expect(within(row).getAllByRole("cell")[0]?.textContent).toBe("11")
  expect(document.activeElement).toBe(header)
  fireEvent.click(screen.getByRole("button", { name: "Next" }))
  await waitFor(() =>
    expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
      "30d",
      10,
      "invocations-cursor",
      "invocations",
      "desc"
    )
  )
  client.clear()
})

// Counts are most interesting highest-first; names and ranks read best ascending.
it.each([
  ["Invocations", "invocations", "desc", "asc"],
  ["Merged PRs / Thread", "merged_prs_per_thread", "desc", "asc"],
  ["User", "user", "asc", "desc"],
] as const)(
  "sorts %s from its natural direction and toggles on the next click",
  async (label, sortKey, first, second) => {
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
    vi.mocked(api.usageLeaderboard).mockResolvedValue({
      ...emptyUsage,
      total_members: 1,
      rows: [costRow],
    })
    const client = mountReport()
    await screen.findByText("Cost Reader")

    for (const direction of [first, second]) {
      fireEvent.click(screen.getByRole("button", { name: label }))
      await waitFor(() =>
        expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
          "30d",
          10,
          undefined,
          sortKey,
          direction
        )
      )
      expect(
        (await screen.findByRole("columnheader", { name: label })).getAttribute(
          "aria-sort"
        )
      ).toBe(direction === "asc" ? "ascending" : "descending")
    }
    client.clear()
  }
)

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
    vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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
    expect(
      within(row.children[6] as HTMLElement).getByText(amount)
    ).toBeTruthy()
    const indicator = within(row).queryByRole("button", {
      name: /Cost (unavailable|incomplete)/,
    })
    expect(indicator?.textContent ?? null).toBe(label)
    client.clear()
  }
)

it("explains incomplete coverage on focus and removes the indicator when costs recover", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
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

function stubClipboard(
  writeText: (text: string) => Promise<void> = () => Promise.resolve()
) {
  const stub = vi.fn().mockImplementation(writeText)
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: stub },
  })
  return stub
}

it("copies an allowlisted diagnostics snapshot to the clipboard", async () => {
  const cohort = {
    model_id: "example-model",
    model_attribution_quality: "configured",
    merged: 0,
    closed_without_merge: 1,
    mature_pending: 0,
    waiting: 0,
    cohort_size: 1,
    decided_denominator: 1,
    decided_merge_rate: 0,
    mature_denominator: 1,
    mature_cohort_merge_share: 0,
    avg_merge_seconds: null,
    efforts: [],
  } as PRMergeRateCohort
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
    report({ ...captured, status: "ready", cohorts: [cohort] })
  )
  const writeText = stubClipboard()
  const client = mountReport()
  fireEvent.click(await screen.findByText("Details"))
  const copy = screen.getByRole("button", { name: "Copy diagnostics" })
  fireEvent.click(copy)
  expect(
    await screen.findByText("Diagnostics copied to clipboard.")
  ).toBeTruthy()

  expect(writeText).toHaveBeenCalledTimes(1)
  const text = writeText.mock.calls[0]?.[0] as string
  const copied = JSON.parse(text)
  expect(copied.report).toBe("open-swe-analytics-diagnostics")
  expect(copied.period).toBe("30d")
  expect(copied.pr_report.fetched_at).toBe(FETCHED_AT)
  expect(copied.pr_report.server_as_of).toBe(captured.as_of)
  expect(copied.event_processing.status).toBe("pending")
  expect(copied.event_processing.reporting_since).toBe(
    captured.reporting_cutover_at
  )
  expect(copied.metrics.avg_delivery_seconds).toEqual({
    state: "unsupported_by_backend",
  })
  // Nothing beyond the allowlisted display state leaves the page.
  expect(text).not.toContain("unavailable_thread_ids")
  expect(text).not.toContain("login")
  expect(text).not.toContain("thread")
  client.clear()
})

it.each([
  [60, 7200],
  [7200, 60],
  [null, 0],
  [null, null],
])(
  "copies retained-report availability without a report average (%s, %s)",
  async (first, second) => {
    const cohort = {
      model_id: "example-model",
      model_attribution_quality: "configured",
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
      avg_delivery_seconds: 7200,
      efforts: [],
    } as PRMergeRateCohort
    const query = vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(
      report({
        ...captured,
        status: "ready",
        cohorts: [
          { ...cohort, avg_delivery_seconds: first },
          { ...cohort, model_id: "other-model", avg_delivery_seconds: second },
        ],
      })
    )
    const writeText = stubClipboard()
    const client = mountReport()
    fireEvent.click(await screen.findByText("Details"))

    // The refresh fails, but the report and its metrics stay on screen.
    query.mockRejectedValue(new ApiError(503, "unavailable"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh now" }))
    expect(
      await screen.findByText(/Last PR report refresh failed/)
    ).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }))
    expect(
      await screen.findByText("Diagnostics copied to clipboard.")
    ).toBeTruthy()
    const copied = JSON.parse(writeText.mock.calls[0]?.[0] as string)
    expect(copied.pr_report.last_refresh_failed).toBe(true)
    expect(copied.metrics.avg_delivery_seconds).toEqual({
      state: first == null && second == null ? "no_valid_samples" : "numeric",
    })
    client.clear()
  }
)

it("copies diagnostics from the keyboard and reports a denied clipboard", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
  const writeText = stubClipboard(() =>
    Promise.reject(new DOMException("denied", "NotAllowedError"))
  )
  const client = mountReport()
  fireEvent.click(await screen.findByText("Details"))
  const copy = screen.getByRole("button", { name: "Copy diagnostics" })
  act(() => copy.focus())
  expect(document.activeElement).toBe(copy)
  fireEvent.keyDown(copy, { key: "Enter" })
  fireEvent.click(copy)
  expect(
    await screen.findByText(
      "Clipboard unavailable. Check the browser's clipboard permission."
    )
  ).toBeTruthy()
  expect(writeText).toHaveBeenCalled()
  client.clear()
})

it("renders feedback counts and resets pagination when sorting feedback in either direction", async () => {
  vi.spyOn(api, "prMergeRateByModel").mockResolvedValue(report(captured))
  vi.mocked(api.usageLeaderboard).mockImplementation(
    async (_period, _limit, cursor) => ({
      ...emptyUsage,
      total_members: 11,
      next_cursor: cursor ? null : "next-page",
      rows: [{ ...costRow, feedback_given: 1234 }],
    })
  )
  const client = mountReport()
  const header = await screen.findByRole("columnheader", {
    name: "# Feedback Given",
  })
  const table = header.closest("table")!
  expect(within(table).getByText("1,234")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Next" }))
  expect(await screen.findByText("Page 2 of 2")).toBeTruthy()
  fireEvent.click(within(header).getByRole("button"))
  await waitFor(() =>
    expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
      "30d",
      10,
      undefined,
      "feedback_given",
      "desc"
    )
  )
  expect(await screen.findByText("Page 1 of 2")).toBeTruthy()
  expect(header.getAttribute("aria-sort")).toBe("descending")
  fireEvent.click(within(header).getByRole("button"))
  await waitFor(() =>
    expect(api.usageLeaderboard).toHaveBeenLastCalledWith(
      "30d",
      10,
      undefined,
      "feedback_given",
      "asc"
    )
  )
  expect(header.getAttribute("aria-sort")).toBe("ascending")
  fireEvent.click(screen.getByRole("button", { name: "threads" }))
  expect(within(table).getByText("1,234")).toBeTruthy()
  expect(header.getAttribute("aria-sort")).toBe("ascending")
  client.clear()
})
