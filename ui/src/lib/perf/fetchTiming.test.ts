/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import {
  classifyDashboardRequest,
  parseServerTiming,
  withRequestTiming,
} from "./fetchTiming"
import { clearPerfSpans, getPerfSpans, subscribePerfSpans } from "./trace"

afterEach(() => clearPerfSpans())

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

describe("withRequestTiming", () => {
  it("does not measure list requests without explicit opt-in", async () => {
    const timedFetch = withRequestTiming(
      vi.fn<typeof fetch>().mockResolvedValue(new Response())
    )
    await timedFetch("/dashboard/api/threads/page")
    expect(getPerfSpans()).toEqual([])
  })

  it("measures list response headers without exposing request parameters", async () => {
    const response = new Response("{}", { status: 200 })
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response)
    const timedFetch = withRequestTiming(fetchImpl, { measureLists: true })

    for (const path of ["pinned", "repos", "page"]) {
      const url = `/dashboard/api/threads/${path}?repo=private-repository`
      expect(await timedFetch(url)).toBe(response)
    }
    expect(
      getPerfSpans().map(({ name, status, attributes }) => ({
        name,
        status,
        attributes,
      }))
    ).toEqual(
      ["pinned", "repos", "page"].map((endpoint) => ({
        name: "thread_list_request",
        status: "ended",
        attributes: { endpoint, status: 200, outcome: "success" },
      }))
    )
  })

  it("records failures and leaves other requests untouched", async () => {
    const error = new Error("network error")
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockRejectedValueOnce(error)
      .mockResolvedValue(new Response())
    const timedFetch = withRequestTiming(fetchImpl, { measureLists: true })

    const failedResponse = await timedFetch("/dashboard/api/threads/page")
    expect(failedResponse.status).toBe(503)
    await expect(timedFetch("/dashboard/api/threads/repos")).rejects.toBe(error)
    await timedFetch("/dashboard/api/threads/pinned", { method: "POST" })
    await timedFetch("/dashboard/api/threads/pinned/order")
    expect(getPerfSpans().map((span) => span.attributes)).toEqual([
      { endpoint: "page", status: 503, outcome: "error" },
      { endpoint: "repos", outcome: "error" },
    ])
  })

  it("does not fail a request if a perf listener throws", async () => {
    const unsubscribe = subscribePerfSpans(() => {
      throw new Error("listener failed")
    })
    const response = new Response()
    try {
      const timedFetch = withRequestTiming(
        vi.fn<typeof fetch>().mockResolvedValue(response),
        { measureLists: true }
      )
      expect(await timedFetch("/dashboard/api/threads/page")).toBe(response)
    } finally {
      unsubscribe()
    }
  })
})
