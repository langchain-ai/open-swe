/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TableScroll } from "./TableScroll"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("only hints at hidden columns, updating on scroll and content or viewport resize", () => {
  let resize = () => {}
  const disconnect = vi.fn()
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback
      }
      observe() {}
      disconnect = disconnect
    }
  )
  const { container, unmount } = render(
    <TableScroll>
      <table>
        <tbody>
          <tr>
            <td>Result</td>
          </tr>
        </tbody>
      </table>
    </TableScroll>
  )
  const viewport = screen.getByRole("region", { name: "Scrollable table" })
  let width = 300
  Object.defineProperties(viewport, {
    clientWidth: { get: () => 300 },
    scrollWidth: { get: () => width },
  })
  const edge = (side: string) =>
    container.querySelector(`[data-table-scroll-edge="${side}"]`)
  act(() => resize())
  expect(edge("left")).toBeNull()
  expect(edge("right")).toBeNull()
  expect(viewport.hasAttribute("tabindex")).toBe(false)
  width = 900
  act(() => resize())
  expect(edge("left")).toBeNull()
  expect(edge("right")).not.toBeNull()
  expect(viewport.tabIndex).toBe(0)
  viewport.scrollLeft = 200
  fireEvent.scroll(viewport)
  expect(edge("left")).not.toBeNull()
  expect(edge("right")).not.toBeNull()
  viewport.scrollLeft = 600
  fireEvent.scroll(viewport)
  expect(edge("left")).not.toBeNull()
  expect(edge("right")).toBeNull()
  width = 300
  viewport.scrollLeft = 0
  act(() => resize())
  expect(edge("left")).toBeNull()
  expect(edge("right")).toBeNull()
  unmount()
  expect(disconnect).toHaveBeenCalledOnce()
})
