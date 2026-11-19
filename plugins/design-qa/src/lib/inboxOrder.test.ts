import type { PullRequestSummary } from "@openswe/plugin-sdk"
import { expect, it } from "vitest"

import type { FrontendReviewRun } from "./capture"
import { InboxOrder } from "./inboxOrder"

const pull = (number: number, createdAt: string, updatedAt: string) =>
  ({
    repository: "acme/web",
    number,
    createdAt,
    updatedAt,
  }) as PullRequestSummary

const run = (score: number | null, errors = 0) =>
  ({
    report: {
      risk: score === null ? null : { score, reason: "" },
      design_system: {
        sources: [],
        findings: Array.from({ length: errors }, () => ({ severity: "error" })),
      },
    },
  }) as unknown as FrontendReviewRun

const pulls = [
  pull(1, "2026-11-01T00:00:00Z", "2026-11-10T00:00:00Z"),
  pull(2, "2026-11-03T00:00:00Z", "2026-11-12T00:00:00Z"),
  pull(3, "2026-11-02T00:00:00Z", "2026-11-11T00:00:00Z"),
  pull(4, "2026-11-04T00:00:00Z", "2026-11-09T00:00:00Z"),
]
const runs = new Map([
  [1, run(2)],
  [2, run(5, 1)],
  [3, run(4, 3)],
])
const order = (sort: Parameters<typeof InboxOrder.sorted>[2]) =>
  InboxOrder.sorted(pulls, ({ number }) => runs.get(number), sort).map(
    ({ number }) => number
  )

it("orders the inbox by time, risk or design system issues, unranked last", () => {
  expect(order("updated")).toEqual([2, 3, 1, 4])
  expect(order("newest")).toEqual([4, 2, 3, 1])
  expect(order("oldest")).toEqual([1, 3, 2, 4])
  // Pull request 4 has no capture, so no risk to rank it by.
  expect(order("risk-high")).toEqual([2, 3, 1, 4])
  expect(order("risk-low")).toEqual([1, 3, 2, 4])
  expect(order("design-system")).toEqual([3, 2, 1, 4])
})

it("reads saved preferences, falling back on what it doesn't know", () => {
  expect(InboxOrder.read({ pageSize: 50, sort: "risk-high" })).toEqual({
    pageSize: 50,
    sort: "risk-high",
  })
  expect(InboxOrder.read({ pageSize: 7, sort: "alphabetical" })).toEqual(
    InboxOrder.defaults
  )
  expect(InboxOrder.read(undefined)).toEqual(InboxOrder.defaults)
})
