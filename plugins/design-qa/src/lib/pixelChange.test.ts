import { expect, it } from "vitest"

import { StoredCapture } from "./capture"
import { runOverview } from "./coverage"
import { PixelChange, type Pixels } from "./pixelChange"
import { DesignQa } from "./queries"

const pixels = (rgba: number[][], width: number): Pixels => ({
  width,
  height: rgba.length / width,
  data: new Uint8ClampedArray(rgba.flat()),
})

it("counts pixels that differ in any channel, alpha included", () => {
  const before = pixels(
    [
      [255, 255, 255, 255],
      [255, 255, 255, 255],
      [0, 0, 0, 255],
      [10, 20, 30, 255],
    ],
    2
  )
  const after = pixels(
    [
      [255, 255, 255, 255],
      [255, 255, 254, 255],
      [0, 0, 0, 0],
      [10, 20, 30, 255],
    ],
    2
  )

  const change = PixelChange.of(before, after)

  expect(change).toMatchObject({ changedPixels: 2, totalPixels: 4 })
  expect(change?.ratio).toBe(0.5)
})

it("compares only screenshots of one size", () => {
  const square = pixels([[0, 0, 0, 255]], 1)
  const wide = pixels(
    [
      [0, 0, 0, 255],
      [0, 0, 0, 255],
    ],
    2
  )

  expect(PixelChange.of(square, wide)).toBeNull()
  expect(PixelChange.of(square, square)?.ratio).toBe(0)
})

it("hides strictly below 0.1%, preserving the boundary and unknown comparisons", () => {
  const before = { width: 1001, height: 1, data: new Uint8ClampedArray(4004) }
  const after = { ...before, data: before.data.slice() }
  after.data[0] = 1
  const below = PixelChange.of(before, after)
  const boundary = PixelChange.of(
    { ...before, width: 1000 },
    { ...after, width: 1000 }
  )

  expect(PixelChange.visible([below])).toBe(false)
  expect(boundary?.ratio).toBe(0.001)
  expect(PixelChange.visible([boundary])).toBe(true)
  expect(PixelChange.visible([below, null])).toBe(true)
  expect(PixelChange.visible([below, undefined])).toBe(true)
  expect(PixelChange.visible([])).toBe(true)
})

it("filters cards, counts and covers only after every captured theme compares below the cutoff", () => {
  const run = StoredCapture.read({
    key: "pulls/7/run",
    updatedAt: null,
    document: {
      status: "complete",
      report: {
        scenarios: [
          { id: "light-only", before_url: "base", after_url: "head" },
          {
            id: "both-low",
            before_url: "base",
            after_url: "head",
            before_dark_url: "base-dark",
            after_dark_url: "head-dark",
          },
          {
            id: "dark-changed",
            before_url: "base",
            after_url: "head",
            before_dark_url: "base-dark",
            after_dark_url: "head-dark",
          },
          {
            id: "partial-dark",
            before_url: "base",
            after_url: "head",
            before_dark_url: "base-dark",
          },
          { id: "mismatch", before_url: "base", after_url: "head" },
          { id: "pending", before_url: "base", after_url: "head" },
          { id: "missing", before_url: "base" },
          {
            id: "failed",
            status: "failed",
            before_url: "base",
            after_url: "head",
          },
        ],
      },
    },
  })
  const report = run.report
  if (!report) throw new Error("The fixture must include a report")
  const scenarios = report.scenarios
  const before = { width: 1000, height: 1, data: new Uint8ClampedArray(4000) }
  const after = { ...before, data: before.data.slice() }
  after.data[0] = 1
  const low = PixelChange.of(before, before)
  const boundary = PixelChange.of(before, after)
  const changes = new Map(
    scenarios.map(
      (scenario) =>
        [
          scenario,
          scenario.id === "both-low"
            ? [low, low]
            : scenario.id === "dark-changed"
              ? [low, boundary]
              : scenario.id === "partial-dark"
                ? [low, undefined]
                : scenario.id === "mismatch"
                  ? [null]
                  : scenario.id === "pending" || scenario.id === "missing"
                    ? [undefined]
                    : [low],
        ] as const
    )
  )

  const filtered = PixelChange.filter(run, changes)

  expect(filtered.report?.scenarios.map(({ id }) => id)).toEqual([
    "dark-changed",
    "partial-dark",
    "mismatch",
    "pending",
    "missing",
    "failed",
  ])
  expect(runOverview(filtered)).toMatchObject({ total: 6, paired: 4 })
  expect(runOverview(filtered).cover?.id).toBe("dark-changed")
  expect(run.report?.scenarios).toHaveLength(8)
  const onlyLow = PixelChange.filter(
    { ...run, report: { ...report, scenarios: scenarios.slice(0, 2) } },
    changes
  )
  const captures = { runs: new Map([[7, onlyLow]]), outcomes: new Map() }
  expect(DesignQa.hidden(captures, 7)).toBe(true)
  expect(DesignQa.hidden(undefined, 7)).toBe(false)
  expect(
    DesignQa.hidden({ ...captures, runs: new Map([[7, filtered]]) }, 7)
  ).toBe(false)
})
