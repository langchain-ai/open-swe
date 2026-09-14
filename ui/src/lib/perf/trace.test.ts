import { afterEach, describe, expect, it, vi } from "vitest"

import {
  clearPerfSpans,
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
    const unregister = registerPerfSink((span) => ended.push(span))
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
    const unregister = registerPerfSink(sink)

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
})
