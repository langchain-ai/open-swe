/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ComparisonViewport } from "./ComparisonViewport"

const panes = [
  { id: "before", label: "Before", content: <canvas /> },
  { id: "after", label: "After", content: <canvas /> },
]

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(400)
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300)
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId = 1
    }
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("keeps both screenshots aligned when scrolling and dragging at native size", () => {
  render(<ComparisonViewport width={1000} height={1000} panes={panes} />)
  fireEvent.click(screen.getByRole("button", { name: "100%" }))
  const before = screen.getByRole("region", {
    name: "Before screenshot viewport",
  })
  const after = screen.getByRole("region", {
    name: "After screenshot viewport",
  })
  before.setPointerCapture = vi.fn()
  before.hasPointerCapture = () => false
  expect(before.firstElementChild?.getAttribute("style")).toContain(
    "width: 1000px"
  )

  before.scrollLeft = 370
  before.scrollTop = 200
  fireEvent.scroll(before)
  expect([after.scrollLeft, after.scrollTop]).toEqual([370, 200])
  fireEvent.pointerDown(before, { clientX: 100, clientY: 100, button: 0 })
  fireEvent.pointerMove(before, { clientX: 50, clientY: 70 })
  fireEvent.pointerUp(before, { clientX: 50, clientY: 70 })
  expect([before.scrollLeft, before.scrollTop]).toEqual([420, 230])
  expect([after.scrollLeft, after.scrollTop]).toEqual([420, 230])
})

it("centers and magnifies a changed region in both panes, then fits the full image", () => {
  const { rerender } = render(
    <ComparisonViewport
      width={1000}
      height={1000}
      panes={panes}
      focus={{ x: 600, y: 700, width: 100, height: 80 }}
    />
  )
  const viewports = screen.getAllByRole("region")
  for (const viewport of viewports) {
    expect(viewport.firstElementChild?.getAttribute("style")).toContain(
      "width: 3000px"
    )
    expect([viewport.scrollLeft, viewport.scrollTop]).toEqual([1750, 2070])
  }
  fireEvent.click(screen.getByRole("button", { name: "Fit" }))
  for (const viewport of viewports)
    expect(viewport.firstElementChild?.getAttribute("style")).toContain(
      "width: 300px"
    )
  rerender(
    <ComparisonViewport
      width={1000}
      height={1000}
      panes={panes}
      focus={{ x: 600, y: 700, width: 100, height: 80 }}
    />
  )
  for (const viewport of viewports) {
    expect(viewport.firstElementChild?.getAttribute("style")).toContain(
      "width: 3000px"
    )
    expect([viewport.scrollLeft, viewport.scrollTop]).toEqual([1750, 2070])
  }
})

it("converts a reverse drag to clamped source pixels and ignores tiny or canceled selections", () => {
  const onSelect = vi.fn()
  render(
    <ComparisonViewport
      width={1000}
      height={1000}
      panes={panes}
      interaction="select"
      onSelect={onSelect}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: "200%" }))
  const viewport = screen.getByRole("region", {
    name: "After screenshot viewport",
  })
  viewport.setPointerCapture = vi.fn()
  viewport.hasPointerCapture = () => false
  const source = viewport.firstElementChild!
  vi.spyOn(source, "getBoundingClientRect").mockReturnValue({
    x: -300,
    y: -200,
    left: -300,
    top: -200,
    width: 2000,
    height: 2000,
    right: 1700,
    bottom: 1800,
    toJSON: () => ({}),
  })
  fireEvent.pointerDown(viewport, { clientX: 100, clientY: 100, button: 0 })
  fireEvent.pointerMove(viewport, { clientX: -400, clientY: -300 })
  fireEvent.pointerUp(viewport, { clientX: -400, clientY: -300 })
  expect(onSelect).toHaveBeenCalledExactlyOnceWith({
    x: 0,
    y: 0,
    width: 200,
    height: 150,
  })

  fireEvent.pointerDown(viewport, { clientX: 100, clientY: 100, button: 0 })
  fireEvent.pointerUp(viewport, { clientX: 102, clientY: 102 })
  fireEvent.pointerDown(viewport, { clientX: 100, clientY: 100, button: 0 })
  fireEvent.pointerCancel(viewport, { clientX: 200, clientY: 200 })
  expect(onSelect).toHaveBeenCalledTimes(1)
})
