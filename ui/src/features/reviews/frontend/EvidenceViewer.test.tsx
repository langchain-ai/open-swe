/** @vitest-environment jsdom */
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { EvidenceViewer } from "./EvidenceViewer"
import type { FrontendScenario } from "./api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("defers offscreen screenshots and aborts requests when their source changes or unmounts", () => {
  let enterViewport!: () => void
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
        enterViewport = () => callback([{ isIntersecting: true }])
      }
      observe() {}
      disconnect() {}
    }
  )
  const signals: AbortSignal[] = []
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((_url, init) => {
      signals.push(init!.signal!)
      return new Promise<Response>(() => {})
    })
  const media = "/dashboard/api/reviews/acme/app/7/frontend-reviews/run/media/"
  const scenario: FrontendScenario = {
    id: "button",
    title: "Button",
    path: "/",
    status: "complete",
    before_url: `${media}before.png`,
    after_url: `${media}after.png`,
    diff_url: null,
    mask_url: null,
    before_video_url: null,
    after_video_url: null,
    width: 100,
    height: 100,
    metrics: null,
    regions: [],
    mask_rects: [],
    tile_size: 32,
    min_region_pixels: 4,
    annotations: [],
    error: null,
  }
  const view = render(<EvidenceViewer scenario={scenario} />)
  expect(fetch).not.toHaveBeenCalled()
  act(() => enterViewport())
  expect(signals).toHaveLength(2)
  view.rerender(
    <EvidenceViewer
      scenario={{ ...scenario, after_url: `${media}updated.png` }}
    />
  )
  expect(signals.slice(0, 2).every((signal) => signal.aborted)).toBe(true)
  expect(signals.slice(2).every((signal) => !signal.aborted)).toBe(true)
  view.unmount()
  expect(signals).toHaveLength(4)
  expect(signals.every((signal) => signal.aborted)).toBe(true)
})
