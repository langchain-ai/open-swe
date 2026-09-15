import { describe, expect, it, vi } from "vitest"

import { withThreadStateView } from "./langgraph-client"

const STATE_URL = "http://localhost:3000/dashboard/api/threads/abc/state"

describe("withThreadStateView", () => {
  it("routes the hydration read to the trimmed view", async () => {
    const inner = vi.fn<typeof fetch>(async () => new Response("{}"))
    await withThreadStateView(inner, "trimmed")(STATE_URL)
    expect(inner).toHaveBeenCalledWith(`${STATE_URL}?view=trimmed`, undefined)
  })

  it("leaves other requests, queries and methods alone", async () => {
    const inner = vi.fn<typeof fetch>(async () => new Response("{}"))
    const fetchTrimmed = withThreadStateView(inner, "trimmed")
    await fetchTrimmed(`${STATE_URL}?subgraphs=true`)
    await fetchTrimmed(STATE_URL, { method: "POST" })
    await fetchTrimmed("http://localhost:3000/dashboard/api/threads/abc")
    expect(inner.mock.calls.map((call) => call[0])).toEqual([
      `${STATE_URL}?subgraphs=true`,
      STATE_URL,
      "http://localhost:3000/dashboard/api/threads/abc",
    ])
  })

  it("is the identity for the full view", () => {
    const inner = vi.fn<typeof fetch>()
    expect(withThreadStateView(inner, "full")).toBe(inner)
  })
})
