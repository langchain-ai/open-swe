import { describe, expect, it } from "vitest"

import { comparePixels, cropRegion } from "./pixels"

describe("frontend changed pixels", () => {
  it("detects faint color shifts at exact sensitivity and a two-pixel size change", () => {
    const before = new Uint8ClampedArray(40 * 40 * 4).fill(255)
    for (let y = 10; y < 24; y++)
      for (let x = 10; x < 30; x++)
        before.set([36, 74, 63, 255], (y * 40 + x) * 4)
    const color = new Uint8ClampedArray(before)
    for (let y = 10; y < 24; y++)
      for (let x = 10; x < 30; x++)
        color.set([44, 82, 71, 255], (y * 40 + x) * 4)
    expect(
      comparePixels(before, color, 40, 40, { threshold: 0 }).changedPixels
    ).toBe(280)
    expect(
      comparePixels(before, color, 40, 40, { threshold: 18 }).changedPixels
    ).toBe(0)

    const taller = new Uint8ClampedArray(before)
    for (let y = 24; y < 26; y++)
      for (let x = 10; x < 30; x++)
        taller.set([36, 74, 63, 255], (y * 40 + x) * 4)
    expect(comparePixels(before, taller, 40, 40).changedPixels).toBe(40)
  })

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
