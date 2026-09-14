/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import { classifyDashboardRequest, parseServerTiming } from "./fetchTiming"

const THREAD = "0d5a5a4e-1b2c-4d3e-8f90-123456789abc"

describe("parseServerTiming", () => {
  it("reads durations", () => {
    expect(
      parseServerTiming(
        'thread_get;dur=12.4, runs_list;dur=30, total;dur=48.1, cache;desc="hit"'
      )
    ).toEqual([
      { name: "thread_get", duration: 12.4 },
      { name: "runs_list", duration: 30 },
      { name: "total", duration: 48.1 },
      { name: "cache", duration: null },
    ])
    expect(parseServerTiming(null)).toEqual([])
  })
})

describe("classifyDashboardRequest", () => {
  it("recognises the thread requests that gate a thread view", () => {
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}`)
    ).toEqual({ kind: "thread_detail", threadId: THREAD })
    expect(
      classifyDashboardRequest(
        `https://example.com/dashboard/api/threads/${THREAD.toUpperCase()}?mark_viewed=false`
      )
    ).toEqual({ kind: "thread_detail", threadId: THREAD })
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/state`)?.kind
    ).toBe("thread_state")
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/stream/events`)
        ?.kind
    ).toBe("stream_events")
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/commands/`)
        ?.kind
    ).toBe("command")
    expect(
      classifyDashboardRequest("/dashboard/api/threads/page?limit=10")
    ).toBe(null)
    expect(
      classifyDashboardRequest(`/dashboard/api/threads/${THREAD}/branch-diff`)
    ).toBe(null)
  })
})
