import { describe, expect, it } from "vitest"

import { pagePoint, stageLayout } from "./browserInput"

const PAGE = { width: 1280, height: 800 }
const NARROW_STAGE = { width: 540, height: 760 }

describe("stageLayout", () => {
  it("lands a click where the pointer is while following it", () => {
    for (const x of [0, 0.25, 0.5, 1]) {
      const layout = stageLayout(NARROW_STAGE, PAGE, "follow", { x, y: 0.5 })
      const frame = {
        left: layout.left,
        top: layout.top,
        width: PAGE.width * layout.scale,
        height: PAGE.height * layout.scale,
      }
      const clicked = pagePoint(x * NARROW_STAGE.width, 380, frame, {
        deviceWidth: PAGE.width,
        deviceHeight: PAGE.height,
      })

      expect(clicked.x).toBeCloseTo(x * PAGE.width)
    }
  })

  it("zooms a wide page into a narrow stage but never past one to one", () => {
    const follow = stageLayout(NARROW_STAGE, PAGE, "follow", { x: 0, y: 0 })
    const fit = stageLayout(NARROW_STAGE, PAGE, "fit", { x: 0, y: 0 })

    expect(fit.scale).toBeCloseTo(540 / 1280)
    expect(follow.scale).toBeCloseTo(760 / 800)
    expect(
      stageLayout({ width: 540, height: 1600 }, PAGE, "follow", {
        x: 0,
        y: 0,
      }).scale
    ).toBe(1)
  })
})
