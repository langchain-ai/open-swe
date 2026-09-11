/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api, ApiError, type PRMergeRatePayload } from "@/lib/api"

import { PRMergeRateSection } from "./usage"

const captured: PRMergeRatePayload = {
  status: "no_prs",
  metric: "pr_outcomes_by_opening_invocation_configured_model",
  definition: "PR outcomes",
  maturity_days: 21,
  period: "30d",
  suppression_threshold: 5,
  cohorts: [],
  collection_started_at: "2026-09-11T12:00:00Z",
  last_processed_at: null,
  data_source: "event_projections",
  completeness: "observed_events_only",
  has_pending_events: true,
  has_failed_events: false,
  as_of: "2026-09-11T12:01:00Z",
}

function mountReport() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  render(
    <QueryClientProvider client={client}>
      <PRMergeRateSection period="30d" login="reader" isAdmin={false} />
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
  expect(screen.getByText(/Collection began/)).toBeTruthy()
  expect(screen.getByText(/after 21 days/)).toBeTruthy()
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
  expect(screen.queryByText(/Collection began/)).toBeNull()

  query.mockResolvedValue({
    ...captured,
    status: "not_started",
    collection_started_at: null,
    completeness: "not_started",
    has_pending_events: false,
  })
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))
  expect(
    await screen.findByText(/No analytics events have been captured/)
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
