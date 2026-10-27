import { describe, expect, it } from "vitest"

import { comparePixels, cropRegion } from "./pixels"

describe("frontend changed pixels", () => {
  it("keeps unchanged pixels transparent and excludes masked changes from coverage and regions", () => {
    const before = new Uint8ClampedArray(12 * 4).fill(255)
    const after = new Uint8ClampedArray(before)
    after.set([0, 0, 0, 255], 0)
    after.set([0, 0, 0, 255], 8)
    const result = comparePixels(before, after, 4, 3, {
      maskRects: [{ x: 0, y: 0, width: 1, height: 1 }],
      minRegionPixels: 1,
      tileSize: 1,
    })
    expect(result.changedPixels).toBe(1)
    expect(result.totalPixels).toBe(11)
    expect(Array.from(result.mask.slice(0, 8))).toEqual([
      0, 0, 0, 0, 0, 0, 0, 0,
    ])
    expect(Array.from(result.mask.slice(8, 12))).toEqual([226, 61, 117, 255])
    expect(result.regions).toEqual([
      { x: 2, y: 0, width: 1, height: 1, changedPixels: 1 },
    ])
    expect(
      comparePixels(before, after, 4, 3, { threshold: 255 }).changedPixels
    ).toBe(0)
  })

  it("joins changes across adjacent tiles and keeps the shared crop within the screenshot", () => {
    const before = new Uint8ClampedArray(100 * 80 * 4).fill(255)
    const after = new Uint8ClampedArray(before)
    after.set([0, 0, 0, 255], (79 * 100 + 98) * 4)
    after.set([0, 0, 0, 255], (79 * 100 + 99) * 4)
    const result = comparePixels(before, after, 100, 80, {
      minRegionPixels: 1,
      tileSize: 1,
    })
    expect(result.regions).toEqual([
      { x: 98, y: 79, width: 2, height: 1, changedPixels: 2 },
    ])
    expect(cropRegion(result.regions[0]!, 100, 80)).toEqual({
      x: 0,
      y: 0,
      width: 100,
      height: 80,
    })
  })
})
