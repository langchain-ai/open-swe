import { expect, it } from "vitest"

import { PixelChange, type Pixels } from "./pixelChange"

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
