import { expect, it } from "vitest"

import type { FrontendReviewRun, FrontendScenario } from "./capture"
import { runOverview } from "./coverage"

const scenario = (
  id: string,
  overrides: Partial<FrontendScenario> = {}
): FrontendScenario => ({
  id,
  title: id,
  path: "/",
  status: "complete",
  before_url: `${id}-before.png`,
  after_url: `${id}-after.png`,
  before_video_url: null,
  after_video_url: null,
  width: 100,
  height: 100,
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

it("counts paired states and interactions, and covers the card with the first pair", () => {
  const overview = runOverview(
    run([
      scenario("route"),
      scenario("hover", { interaction: true }),
      scenario("dialog", { interaction: true, after_url: null }),
    ])
  )
  expect(overview).toMatchObject({ total: 3, paired: 2, interactions: 1 })
  expect(overview.cover?.id).toBe("route")
})

it("pairs neither blocked nor one-sided captures", () => {
  expect(
    runOverview(
      run([
        scenario("blocked", { status: "blocked" }),
        scenario("missing", { before_url: null }),
      ])
    )
  ).toMatchObject({ total: 2, paired: 0, interactions: 0, cover: undefined })
})
