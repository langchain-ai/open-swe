import { describe, expect, it } from "vitest"

import {
  BROWSER_DEVICE_TOOLBAR_HEIGHT,
  BROWSER_VIEWPORT_MAX_AREA,
  BROWSER_VIEWPORT_MIN_DIMENSION,
  BROWSER_VIEWPORT_RESIZE_RAIL_SIZE,
  FILL_VIEWPORT,
  parseBrowserViewport,
  presetViewport,
  resizeBrowserViewportFromRail,
  resizeFreeformViewport,
  resolveBrowserDeviceViewportLayout,
  resolveBrowserViewportLayout,
  resolveResponsiveBrowserViewportSize,
  stepBrowserZoom,
} from "@/features/agents/browser/browserViewport"

describe("browser viewport layout", () => {
  it("fills the panel in fill mode", () => {
    expect(
      resolveBrowserViewportLayout({ width: 800, height: 600 }, FILL_VIEWPORT)
    ).toMatchObject({
      viewportWidth: 800,
      viewportHeight: 600,
      viewportScale: 1,
      fillsPanel: true,
    })
  })

  it("scales a fixed viewport down to fit and centres it", () => {
    const layout = resolveBrowserViewportLayout(
      { width: 400, height: 800 },
      { mode: "freeform", width: 800, height: 800 }
    )
    expect(layout.viewportScale).toBe(0.5)
    expect(layout.viewportWidth).toBe(400)
    expect(layout.viewportHeight).toBe(400)
    expect(layout.viewportY).toBe(200)
    expect(layout.fillsPanel).toBe(false)
  })

  it("never scales a small viewport up, and zoom enlarges the footprint", () => {
    const unzoomed = resolveBrowserViewportLayout(
      { width: 1000, height: 1000 },
      { mode: "freeform", width: 400, height: 400 }
    )
    expect(unzoomed.viewportScale).toBe(1)
    expect(unzoomed.viewportX).toBe(300)
    const zoomed = resolveBrowserViewportLayout(
      { width: 1000, height: 1000 },
      { mode: "freeform", width: 400, height: 400 },
      2
    )
    expect(zoomed.viewportWidth).toBe(800)
  })

  it("reserves room for the device toolbar and rails", () => {
    const layout = resolveBrowserDeviceViewportLayout(
      { width: 500, height: 500 },
      { mode: "freeform", width: 300, height: 300 }
    )
    expect(layout.viewportY).toBeGreaterThanOrEqual(
      BROWSER_DEVICE_TOOLBAR_HEIGHT
    )
    expect(layout.viewportX).toBeGreaterThanOrEqual(
      BROWSER_VIEWPORT_RESIZE_RAIL_SIZE
    )
    expect(layout.canvasWidth).toBe(500)
  })
})

describe("browser viewport resizing", () => {
  it("clamps to the allowed dimensions and area", () => {
    expect(
      resizeFreeformViewport(
        { width: 300, height: 300 },
        { x: -1000, y: -1000 }
      )
    ).toEqual({
      width: BROWSER_VIEWPORT_MIN_DIMENSION,
      height: BROWSER_VIEWPORT_MIN_DIMENSION,
    })
    const huge = resizeFreeformViewport(
      { width: 3800, height: 2000 },
      { x: 0, y: 500 }
    )
    expect(huge.width * huge.height).toBeLessThanOrEqual(
      BROWSER_VIEWPORT_MAX_AREA
    )
  })

  it("divides deltas by the zoom factor and honours the drag direction", () => {
    expect(
      resizeFreeformViewport(
        { width: 400, height: 400 },
        { x: 100, y: 0 },
        2,
        "east"
      )
    ).toEqual({ width: 450, height: 400 })
    expect(
      resizeFreeformViewport(
        { width: 400, height: 400 },
        { x: -50, y: 0 },
        1,
        "west"
      )
    ).toEqual({ width: 450, height: 400 })
  })

  it("keeps a locked aspect ratio", () => {
    const resized = resizeFreeformViewport(
      { width: 400, height: 800 },
      { x: 100, y: 0 },
      1,
      "east",
      0.5
    )
    expect(resized.width / resized.height).toBeCloseTo(0.5, 2)
  })

  it("grows a centred viewport from both edges when dragged from a rail", () => {
    const resized = resizeBrowserViewportFromRail(
      { width: 400, height: 400 },
      { x: 50, y: 0 },
      { width: 1000, height: 1000 },
      1,
      "east"
    )
    expect(resized).toEqual({ width: 500, height: 400 })
  })

  it("fits the responsive viewport to the available area", () => {
    const size = resolveResponsiveBrowserViewportSize({
      width: 800,
      height: 600,
    })
    expect(size.width).toBe(800 - BROWSER_VIEWPORT_RESIZE_RAIL_SIZE * 2)
    expect(size.height).toBe(
      600 - BROWSER_DEVICE_TOOLBAR_HEIGHT - BROWSER_VIEWPORT_RESIZE_RAIL_SIZE
    )
  })
})

describe("presets, parsing, and zoom", () => {
  it("rotates presets on request", () => {
    expect(presetViewport("iphone-se")).toMatchObject({
      width: 375,
      height: 667,
    })
    expect(presetViewport("iphone-se", "landscape")).toMatchObject({
      width: 667,
      height: 375,
    })
    expect(presetViewport("nest-hub", "portrait")).toMatchObject({
      width: 600,
      height: 1024,
    })
  })

  it("re-validates persisted viewports and falls back to fill", () => {
    expect(parseBrowserViewport(null)).toEqual(FILL_VIEWPORT)
    expect(
      parseBrowserViewport({ mode: "freeform", width: 10, height: 10 })
    ).toEqual(FILL_VIEWPORT)
    expect(
      parseBrowserViewport({
        mode: "preset",
        width: 375,
        height: 667,
        presetId: "nope",
      })
    ).toEqual({ mode: "freeform", width: 375, height: 667 })
    expect(
      parseBrowserViewport({
        mode: "preset",
        width: 375,
        height: 667,
        presetId: "iphone-se",
      })
    ).toEqual({
      mode: "preset",
      width: 375,
      height: 667,
      presetId: "iphone-se",
    })
  })

  it("steps zoom along the ladder and clamps at the ends", () => {
    expect(stepBrowserZoom(1, 1)).toBe(1.1)
    expect(stepBrowserZoom(1, -1)).toBe(0.9)
    expect(stepBrowserZoom(5, 1)).toBe(5)
    expect(stepBrowserZoom(0.25, -1)).toBe(0.25)
    // Off-ladder values snap to the next level in the requested direction.
    expect(stepBrowserZoom(1.05, 1)).toBe(1.1)
    expect(stepBrowserZoom(1.05, -1)).toBe(1)
  })
})
