/** @vitest-environment jsdom */
import { afterEach, expect, it, vi } from "vitest"

import { DashboardRequestError } from "./dashboard-fetch"

const rum = vi.hoisted(() => ({
  ready: false,
  addError: vi.fn(),
  initialized: () => {},
}))
vi.mock("./datadog", () => ({
  getDatadogRum: () => (rum.ready ? { addError: rum.addError } : undefined),
  subscribeToDatadogInitialization: (listener: () => void) => {
    rum.initialized = listener
  },
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it("captures caught HTTP failures before RUM loads, deduplicates them, and does not recurse", async () => {
  const reports: Array<Record<string, unknown>> = []
  const requestId = `req_${crypto.randomUUID()}`
  const originalFetch = vi.fn(
    async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.body) {
        reports.push(JSON.parse(String(init.body)))
        return new Response(null, { status: 503 })
      }
      return new Response(null, { status: 500 })
    }
  )
  vi.stubGlobal("fetch", originalFetch)
  vi.spyOn(console, "warn").mockImplementation(() => {})
  const { installClientErrorReporting, recordClientError } =
    await import("./clientErrors")
  installClientErrorReporting()

  const response = await fetch("/dashboard/api/threads?token=secret", {
    headers: { "X-Request-ID": requestId },
  })
  expect(response.status).toBe(500)
  recordClientError({
    title: "Query failed",
    error: new DashboardRequestError(500, "Failed", requestId),
  })
  await vi.waitFor(() => expect(reports).toHaveLength(1))
  expect(reports[0]).toMatchObject({ error_id: requestId, status: 500 })
  expect(JSON.stringify(reports)).not.toContain("secret")
  expect(originalFetch).toHaveBeenCalledTimes(2)
  expect(rum.addError).not.toHaveBeenCalled()

  rum.ready = true
  rum.initialized()
  expect(rum.addError).toHaveBeenCalledTimes(1)
  expect(rum.addError).toHaveBeenCalledWith(
    expect.any(Error),
    expect.objectContaining({ error_id: requestId })
  )

  const renderError = new Error("Render failed")
  recordClientError({ title: "React render failed", error: renderError })
  window.dispatchEvent(new ErrorEvent("error", { error: renderError }))
  expect(reports).toHaveLength(2)
  expect(rum.addError).toHaveBeenCalledTimes(2)

  window.dispatchEvent(
    new ErrorEvent("error", { error: new Error("Uncaught") })
  )
  expect(reports).toHaveLength(3)
  expect(rum.addError).toHaveBeenCalledTimes(2)

  recordClientError({
    title: "Cancelled",
    error: new DOMException("Cancelled", "AbortError"),
  })
  expect(reports).toHaveLength(3)
})
