/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { recordRequestTiming } from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"
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
import type { PerfThreadSource } from "./trace"

const THREAD_A = "0d5a5a4e-1b2c-4d3e-8f90-123456789abc"
const THREAD_B = "1e6b6b5f-2c3d-4e4f-9a01-23456789abcd"
const STATE_READ: RequestTiming = {
  kind: "thread_state",
  threadId: THREAD_A,
  status: 200,
  ttfbMs: 41.2,
  serverTiming: [],
}
const STATE_PARSED: RequestTiming = {
  ...STATE_READ,
  bodyMs: 21.3,
  parseMs: 4.4,
}
const RUNNING_LOOKUP: RequestTiming = {
  kind: "running_lookup",
  threadId: THREAD_A,
  status: 200,
  ttfbMs: 0,
  serverTiming: [],
}
const PENDING_LOOKUP: RequestTiming = {
  ...RUNNING_LOOKUP,
  kind: "pending_lookup",
}
const STREAM_PAINT = {
  messages: 1,
  chunks: 1,
  hasOlder: false,
  threadSource: "stream",
  fromSource: true,
  hydration: Promise.resolve(),
} as const
const READS_HANG = new Promise<void>(() => {})

afterEach(() => {
  resetThreadLoadTracking()
  clearPerfSpans()
  vi.useRealTimers()
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
      serverTiming: [{ name: "get_state", duration: 30.6 }],
    })
    threadHydrated(THREAD_A)
    threadTranscriptBuilt(THREAD_A, 3.2)
    threadTranscriptBuilt(THREAD_A, 1.1)
    threadTranscriptPainted(THREAD_A, {
      messages: 7,
      chunks: 21,
      hasOlder: false,
      threadSource: "transcript",
      fromSource: true,
      hydration: Promise.resolve(),
    })

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
      builds: 2,
      build_ms: 4,
      messages: 7,
      chunks: 21,
      thread_source: "transcript",
    })
    expect(getPerfSpans()).toHaveLength(1)
  })

  const CACHE_HIT_CASES: Array<{
    name: string
    reads: Array<RequestTiming>
    threadSource: PerfThreadSource
    fromSource: boolean
    hit: boolean
  }> = [
    {
      name: "stream paint before any read",
      reads: [],
      threadSource: "stream",
      fromSource: true,
      hit: true,
    },
    {
      name: "stream paint while the state body downloads",
      reads: [STATE_READ],
      threadSource: "stream",
      fromSource: true,
      hit: true,
    },
    {
      name: "stream paint of the parsed state",
      reads: [STATE_READ, STATE_PARSED],
      threadSource: "stream",
      fromSource: true,
      hit: false,
    },
    {
      name: "transcript paint of its snapshot",
      reads: [{ ...STATE_READ, kind: "thread_transcript" }],
      threadSource: "transcript",
      fromSource: true,
      hit: false,
    },
    {
      name: "new thread's pending row",
      reads: [],
      threadSource: "stream",
      fromSource: false,
      hit: false,
    },
  ]

  // A cache hit is a paint of the source's own rows before its read answered:
  // the SDK's state read once parsed, the transcript snapshot at its headers.
  it.each(CACHE_HIT_CASES)(
    "$name: thread_cache_hit is $hit",
    async ({ reads, threadSource, fromSource, hit }) => {
      onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
      for (const read of reads) recordRequestTiming(read)
      threadTranscriptPainted(THREAD_A, {
        ...STREAM_PAINT,
        threadSource,
        fromSource,
      })
      await STREAM_PAINT.hydration

      expect(getPerfSpans()[0]?.attributes.thread_cache_hit).toBe(hit)
    }
  )

  it("reports a stream open's state read in phases and its run lookups by when they were sent", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "performance"] })
    let settle = () => {}
    const hydration = new Promise<void>((resolve) => {
      settle = resolve
    })
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    vi.advanceTimersByTime(41)
    recordRequestTiming(STATE_READ)
    vi.advanceTimersByTime(25)
    recordRequestTiming(STATE_PARSED)
    vi.advanceTimersByTime(34)
    threadTranscriptPainted(THREAD_A, { ...STREAM_PAINT, hydration })
    vi.advanceTimersByTime(86)
    recordRequestTiming({ ...RUNNING_LOOKUP, ttfbMs: 120.2 })
    vi.advanceTimersByTime(30)
    recordRequestTiming({ ...PENDING_LOOKUP, ttfbMs: 150.4 })
    vi.advanceTimersByTime(500)
    recordRequestTiming({ ...RUNNING_LOOKUP, ttfbMs: 20 })
    settle()
    await hydration
    const [span] = getPerfSpans()
    expect(span?.status).toBe("open")
    recordRequestTiming({ ...RUNNING_LOOKUP, ttfbMs: 716.6 })

    expect(span).toMatchObject({ status: "ended", duration: 100 })
    expect(span?.attributes).toMatchObject({
      state_ttfb_ms: 41,
      state_body_ms: 21,
      state_parse_ms: 4,
      running_lookup_1_ms: 717,
      running_lookup_2_ms: 120,
      pending_lookup_ms: 150,
      thread_cache_hit: false,
    })
  })

  it("still ends a painted stream open at its paint when it is left or its reads hang", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "performance"] })
    onRouterNavigation(`/agents/${THREAD_A}`, "/agents")
    vi.advanceTimersByTime(40)
    threadTranscriptPainted(THREAD_A, {
      ...STREAM_PAINT,
      hydration: READS_HANG,
    })
    vi.advanceTimersByTime(900)
    recordRequestTiming(STATE_READ)
    onRouterNavigation(`/agents/${THREAD_B}`, `/agents/${THREAD_A}`)
    vi.advanceTimersByTime(60)
    threadTranscriptPainted(THREAD_B, {
      ...STREAM_PAINT,
      hydration: READS_HANG,
    })
    vi.advanceTimersByTime(10_000)

    expect(
      getPerfSpans().map(({ status, duration }) => ({ status, duration }))
    ).toEqual([
      { status: "ended", duration: 40 },
      { status: "ended", duration: 60 },
    ])
    expect(getPerfSpans()[0]?.attributes).not.toHaveProperty("state_ttfb_ms")
  })

  it("tags which thread UI the span measured", () => {
    onRouterNavigation(`/assistant/${THREAD_A}`, "/assistant")
    ensureThreadLoad(THREAD_A, "assistant")
    threadHydrated(THREAD_A)
    threadTranscriptPainted(THREAD_A, {
      messages: 1,
      chunks: 1,
      hasOlder: false,
      threadSource: "stream",
      fromSource: true,
      hydration: Promise.resolve(),
    })
    onRouterNavigation(`/agents/${THREAD_B}`, `/assistant/${THREAD_A}`)
    ensureThreadLoad(THREAD_B)
    threadTranscriptPainted(THREAD_B, {
      messages: 1,
      chunks: 1,
      hasOlder: false,
      threadSource: "stream",
      fromSource: true,
      hydration: Promise.resolve(),
    })

    expect(getPerfSpans().map((span) => span.attributes.ui)).toEqual([
      "assistant",
      "agents",
    ])
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
    threadHydrated(THREAD_B)
    threadHydrationFailed(THREAD_B)
    threadTranscriptPainted(THREAD_B, {
      messages: 3,
      chunks: 3,
      hasOlder: false,
      threadSource: "stream",
      fromSource: true,
      hydration: Promise.resolve(),
    })

    const [span] = getPerfSpans()
    expect(span?.status).toBe("open")
    expect(span?.steps).toEqual([])
  })
})
