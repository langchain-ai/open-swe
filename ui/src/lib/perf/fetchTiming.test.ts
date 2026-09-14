/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest"

import {
  classifyDashboardRequest,
  parseServerTiming,
  subscribeRequestTimings,
  withRequestTiming,
} from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"

const THREAD = "0d5a5a4e-1b2c-4d3e-8f90-123456789abc"

describe("parseServerTiming", () => {
  it("reads durations and descriptions", () => {
    expect(
      parseServerTiming(
        'thread_get;dur=12.4, runs_list;dur=30, total;dur=48.1, cache;desc="hit"'
      )
    ).toEqual([
      { name: "thread_get", duration: 12.4, description: null },
      { name: "runs_list", duration: 30, description: null },
      { name: "total", duration: 48.1, description: null },
      { name: "cache", duration: null, description: "hit" },
    ])
    expect(parseServerTiming(null)).toEqual([])
  })
})

describe("classifyDashboardRequest", () => {
  it("recognises the thread requests that gate a thread view", () => {
    expect(classifyDashboardRequest(`/dashboard/api/threads/${THREAD}`)).toBe(
      "thread_detail"
    )
    expect(
      classifyDashboardRequest(
        `https://example.com/dashboard/api/threads/${THREAD}?mark_viewed=false`
      )
    ).toBe("thread_detail")
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/state`)
    ).toBe("thread_state")
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/stream/events`)
    ).toBe("stream_events")
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/commands`)
    ).toBe("command")
    expect(
      classifyDashboardRequest("/dashboard/api/threads/page?limit=10")
    ).toBe(null)
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/branch-diff`)
    ).toBe(null)
  })
})

describe("withRequestTiming", () => {
  it("reports classified requests with their server timing and leaves others alone", async () => {
    const timings: Array<RequestTiming> = []
    const unsubscribe = subscribeRequestTimings((timing) =>
      timings.push(timing)
    )
    const inner = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "Server-Timing": "thread_get;dur=5, total;dur=9" },
        })
    )
    const timed = withRequestTiming(inner)

    await timed(`/dashboard/api/threads/${THREAD}`, { method: "GET" })
    await timed("/dashboard/api/me")
    unsubscribe()

    expect(inner).toHaveBeenCalledTimes(2)
    expect(timings).toHaveLength(1)
    expect(timings[0]).toMatchObject({
      kind: "thread_detail",
      status: 200,
      serverTiming: [
        { name: "thread_get", duration: 5 },
        { name: "total", duration: 9 },
      ],
    })
    expect(timings[0]?.ttfbMs).toBeGreaterThanOrEqual(0)
  })
})
