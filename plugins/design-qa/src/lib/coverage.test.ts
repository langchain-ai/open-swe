import type { FrontendReviewRun, FrontendScenario } from "@openswe/plugin-sdk"
import { expect, it } from "vitest"

import { runOverview, visualExtent } from "./coverage"

it("buckets the changed-pixel fraction into an extent", () => {
  expect(visualExtent(0)).toBe("none")
  expect(visualExtent(0.0001)).toBe("small")
  expect(visualExtent(0.0199)).toBe("small")
  expect(visualExtent(0.02)).toBe("moderate")
  expect(visualExtent(0.1499)).toBe("moderate")
  expect(visualExtent(0.15)).toBe("large")
  expect(visualExtent(1)).toBe("large")
})

const scenario = (
  id: string,
  changedRatio: number,
  overrides: Partial<FrontendScenario> = {}
): FrontendScenario => ({
  id,
  title: id,
  path: "/",
  status: "complete",
  before_url: `${id}-before.png`,
  after_url: `${id}-after.png`,
  diff_url: null,
  mask_url: null,
  before_video_url: null,
  after_video_url: null,
  width: 100,
  height: 100,
  metrics: {
    changed_pixels: changedRatio * 10_000,
    total_pixels: 10_000,
    changed_ratio: changedRatio,
    threshold: 0,
  },
  regions: [],
  annotations: [],
  mask_rects: [],
  tile_size: 32,
  min_region_pixels: 4,
  interaction: false,
  actions: [],
  before_error: null,
  after_error: null,
  layout_warnings: [],
  error: null,
  ...overrides,
})

const run = (scenarios: FrontendScenario[]) =>
  ({
    report: { scenarios },
  }) as FrontendReviewRun

it("takes the extent from the largest paired state and counts paired interactions", () => {
  const overview = runOverview(
    run([
      scenario("route", 0.01),
      scenario("hover", 0.2, { interaction: true }),
      scenario("dialog", 0.5, { interaction: true, after_url: null }),
    ])
  )
  expect(overview).toMatchObject({
    total: 3,
    paired: 2,
    interactions: 1,
    extent: "large",
  })
  expect(overview.largest?.id).toBe("hover")
})

it("gives blocked or unpaired captures no extent", () => {
  expect(
    runOverview(
      run([
        scenario("blocked", 0.3, { status: "blocked" }),
        scenario("missing", 0, { before_url: null }),
        scenario("unmeasured", 0.1, { metrics: null }),
      ])
    )
  ).toMatchObject({ total: 3, paired: 0, interactions: 0, extent: null })
  expect(runOverview(run([scenario("same", 0)])).extent).toBe("none")
})
