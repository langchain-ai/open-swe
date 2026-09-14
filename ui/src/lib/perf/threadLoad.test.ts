/** @vitest-environment jsdom */

import { afterEach, describe, expect, it } from "vitest"

import { recordRequestTiming } from "./fetchTiming"
import {
  ensureThreadLoad,
  onRouterNavigation,
  resetThreadLoadTracking,
  threadDetailResolved,
  threadHydrated,
  threadHydrationFailed,
  threadTranscriptBuilt,
  threadTranscriptPainted,
} from "./threadLoad"
import { clearPerfSpans, getPerfSpans } from "./trace"

const THREAD_A = "0d5a5a4e-1b2c-4d3e-8f90-123456789abc"
const THREAD_B = "1e6b6b5f-2c3d-4e4f-9a01-23456789abcd"

afterEach(() => {
  resetThreadLoadTracking()
  clearPerfSpans()
})

describe("thread load span", () => {
  it("runs from navigation through paint and folds in request timings", () => {
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    ensureThreadLoad(THREAD_A)
    threadDetailResolved(THREAD_A, { cached: true })
    recordRequestTiming({
      kind: "thread_state",
      threadId: THREAD_A,
      status: 200,
      ttfbMs: 41.2,
      serverTiming: [{ name: "get_state", duration: 30.6, description: null }],
    })
    threadHydrated(THREAD_A, { messages: 7 })
    threadTranscriptBuilt(THREAD_A, 3.2)
    threadTranscriptPainted(THREAD_A, { messages: 7, chunks: 21 })

    const [span] = getPerfSpans()
    expect(span?.status).toBe("ended")
    expect(span?.steps.map((step) => step.name)).toEqual([
      "detail",
      "hydrate",
      "paint",
    ])
    expect(span?.attributes).toMatchObject({
      source: "navigation",
      cold: false,
      detail_cached: true,
      state_ttfb_ms: 41,
      state_status: 200,
      state_srv_get_state_ms: 31,
      hydrated_messages: 7,
      builds: 1,
      messages: 7,
      chunks: 21,
    })
    expect(getPerfSpans()).toHaveLength(1)
  })

  it("treats a mount without prior navigation as a page load from time origin", () => {
    ensureThreadLoad(THREAD_A)
    const [span] = getPerfSpans()
    expect(span?.startedAt).toBe(0)
    expect(span?.attributes).toMatchObject({ source: "page_load", cold: true })
  })

  it("abandons a load the user navigates away from", () => {
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    onRouterNavigation(`/agents/${THREAD_B}`, `/agents/${THREAD_A}`)
    onRouterNavigation("/agents", `/agents/${THREAD_B}`)

    expect(
      getPerfSpans().map((span) => span.attributes["abandoned_reason"])
    ).toEqual(["superseded", "navigated_away"])
  })

  it("ignores steps for a thread that is not being tracked", () => {
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    threadHydrated(THREAD_B, { messages: 3 })
    threadHydrationFailed(THREAD_B)
    threadTranscriptPainted(THREAD_B, { messages: 3, chunks: 3 })

    const [span] = getPerfSpans()
    expect(span?.status).toBe("open")
    expect(span?.steps).toEqual([])
  })

  it("abandons on hydration failure", () => {
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    threadHydrationFailed(THREAD_A)
    expect(getPerfSpans()[0]?.attributes["abandoned_reason"]).toBe(
      "hydration_failed"
    )
  })
})
