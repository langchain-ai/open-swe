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
