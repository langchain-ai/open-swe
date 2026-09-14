import { afterEach, describe, expect, it, vi } from "vitest"

import {
  clearPerfSpans,
  formatSpan,
  getPerfSpans,
  registerPerfSink,
  startSpan,
} from "./trace"
import type { PerfSpan } from "./trace"

afterEach(() => {
  clearPerfSpans()
  vi.restoreAllMocks()
})

describe("startSpan", () => {
  it("records steps relative to the start and hands ended spans to sinks", () => {
    const ended: Array<PerfSpan> = []
    const unregister = registerPerfSink({
      onSpanEnd: (span) => ended.push(span),
    })
    const now = vi.spyOn(performance, "now")
    now.mockReturnValue(1000)

    const span = startSpan("thread_load", { source: "navigation" })
    now.mockReturnValue(1120)
    span.mark("detail", { detail_cached: false })
    span.mark("detail")
    span.add("build_ms", 4)
    span.add("build_ms", 6)
    now.mockReturnValue(1800)
    const result = span.end({ messages: 12 })
    unregister()

    expect(result.status).toBe("ended")
    expect(result.duration).toBe(800)
    expect(result.steps).toEqual([{ name: "detail", at: 120 }])
    expect(result.attributes).toEqual({
      source: "navigation",
      detail_cached: false,
      build_ms: 10,
      messages: 12,
    })
    expect(ended).toEqual([result])
    expect(span.ended).toBe(true)
  })

  it("keeps abandoned spans for the local buffer but not for sinks", () => {
    const sink = vi.fn()
    const unregister = registerPerfSink({ onSpanEnd: sink })

    const span = startSpan("agent_run", { transport: "cloud" })
    span.abandon("unmounted")
    span.mark("late")
    span.end()
    unregister()

    expect(sink).not.toHaveBeenCalled()
    const [retained] = getPerfSpans()
    expect(retained?.status).toBe("abandoned")
    expect(retained?.attributes["abandoned_reason"]).toBe("unmounted")
    expect(retained?.steps).toEqual([])
  })

  it("can start at the document time origin for full page loads", () => {
    const span = startSpan("thread_load", {}, { startedAt: 0 })
    expect(span.elapsed()).toBeGreaterThan(0)
    expect(getPerfSpans()[0]?.startEpochMs).toBeCloseTo(
      performance.timeOrigin,
      -1
    )
  })

  it("formats a one-line summary", () => {
    const span: PerfSpan = {
      id: "thread_load-1",
      name: "thread_load",
      startedAt: 0,
      startEpochMs: 0,
      steps: [
        { name: "detail", at: 120.4 },
        { name: "paint", at: 811.6 },
      ],
      attributes: {},
      status: "ended",
      duration: 811.6,
    }
    expect(formatSpan(span)).toBe("thread_load 812ms — detail 120 · paint 812")
  })
})
