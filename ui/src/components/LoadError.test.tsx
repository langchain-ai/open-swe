/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { importRecovery } from "@/lib/importRecovery"
import { LoadError } from "./LoadError"

const reload = vi.fn()
const replace = vi.fn()
const navigation = new EventTarget()

beforeEach(() => {
  vi.useFakeTimers()
  sessionStorage.clear()
  vi.stubGlobal("navigation", navigation)
  vi.stubGlobal("location", {
    href: "https://robin.langchain.dev/agents/reviews?pr=langchain-ai%2Flangchainplus%2369025&tab=mine#files",
    reload,
    replace,
  })
  vi.spyOn(console, "error").mockImplementation(() => {})
  importRecovery.install()
  window.dispatchEvent(new Event("pageshow"))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  reload.mockClear()
  replace.mockClear()
})

it.each([
  "Loading chunk 1 failed",
  "Failed to fetch dynamically imported module: /assets/Navigator.js",
  "Importing a module script failed",
  "error loading dynamically imported module: /assets/Navigator.js",
])("recovers %s once without losing the selected PR", (message) => {
  const view = render(<LoadError error={new TypeError(message)} />)
  const target = new URL(replace.mock.calls[0]?.[0])
  expect(target.pathname).toBe("/agents/reviews")
  expect(target.searchParams.get("pr")).toBe("langchain-ai/langchainplus#69025")
  expect(target.searchParams.get("tab")).toBe("mine")
  expect(target.hash).toBe("#files")
  expect(target.searchParams.has("refresh")).toBe(true)

  view.rerender(<LoadError error={new TypeError(message)} />)
  expect(replace).toHaveBeenCalledOnce()
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy()

  vi.advanceTimersByTime(30_001)
  view.rerender(<LoadError error={new TypeError(message)} />)
  expect(replace).toHaveBeenCalledTimes(2)
})

it("leaves optional imports alone and bounds required chunk reloads across errors", () => {
  for (let index = 0; index < 4; index++) {
    const error = new TypeError(
      `Failed to fetch dynamically imported module: ${index}.js`
    )
    window.dispatchEvent(
      Object.assign(new Event("vite:preloadError", { cancelable: true }), {
        payload: error,
      })
    )
    expect(reload).toHaveBeenCalledTimes(Math.min(index, 3))
    const view = render(<LoadError error={error} />)
    window.dispatchEvent(Object.assign(new Event("error"), { error }))
    expect(reload).toHaveBeenCalledTimes(Math.min(index + 1, 3))
    expect(screen.getByRole("alert")).toBeTruthy()
    view.unmount()
  }
})

it("ignores optional stylesheet failures and ordinary application errors", () => {
  const css = Object.assign(
    new Event("vite:preloadError", { cancelable: true }),
    {
      payload: new Error("Unable to preload CSS for /assets/page.css"),
    }
  )
  window.dispatchEvent(css)
  expect(css.defaultPrevented).toBe(true)
  render(<LoadError error={new Error("Request failed")} />)
  expect(reload).not.toHaveBeenCalled()
  expect(replace).not.toHaveBeenCalled()
  expect(screen.getByRole("alert").textContent).toContain("Request failed")
})

it("waits for a canceled document navigation before recovering an unhandled preload", async () => {
  const controller = new AbortController()
  navigation.dispatchEvent(
    Object.assign(new Event("navigate", { cancelable: true }), {
      destination: { sameDocument: false },
      signal: controller.signal,
    })
  )
  const error = new Error(
    "Failed to fetch dynamically imported module: page.js"
  )
  window.dispatchEvent(
    Object.assign(new Event("vite:preloadError"), { payload: error })
  )
  window.dispatchEvent(
    Object.assign(new Event("unhandledrejection"), { reason: error })
  )
  await vi.runAllTimersAsync()
  expect(reload).not.toHaveBeenCalled()
  controller.abort()
  await vi.runAllTimersAsync()
  expect(reload).toHaveBeenCalledOnce()
})

it("keeps the error visible when storage cannot guard against reload loops", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Storage unavailable")
  })
  render(
    <LoadError
      error={new Error("Failed to fetch dynamically imported module: page.js")}
    />
  )
  expect(replace).not.toHaveBeenCalled()
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy()
})
