/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { RelativeTimestamp } from "./RelativeTimestamp"

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

it("updates as time passes, retains the full timestamp, and cleans up on replacement and unmount", () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-10-20T12:03:00Z"))
  const value = "2026-10-20T12:00:00Z"
  const { rerender, unmount } = render(<RelativeTimestamp value={value} />)
  const time = screen.getByText("3 minutes ago")
  const title = time.title
  expect(title).toBe(
    new Date(value).toLocaleString(undefined, {
      dateStyle: "full",
      timeStyle: "long",
    })
  )
  expect(time.getAttribute("datetime")).toBe(value)

  act(() => vi.advanceTimersByTime(60_000))
  expect(screen.getByText("4 minutes ago").title).toBe(title)

  rerender(<RelativeTimestamp value="2026-10-20T12:03:50Z" />)
  expect(screen.getByText("10 seconds ago")).toBeTruthy()
  expect(vi.getTimerCount()).toBe(1)
  act(() => vi.advanceTimersByTime(1000))
  expect(screen.getByText("11 seconds ago")).toBeTruthy()

  unmount()
  expect(vi.getTimerCount()).toBe(0)
})
