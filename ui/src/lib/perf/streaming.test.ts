import { afterEach, describe, expect, it, vi } from "vitest"

import { recordRequestTiming } from "./fetchTiming"
import {
  createRunTracker,
  hasOpenRuns,
  runTranscriptBuilt,
  runTranscriptCommitted,
} from "./streaming"
import { clearPerfSpans, getPerfSpans } from "./trace"

function lifecycle(event: string, timestamp: number) {
  return {
    type: "event",
    method: "lifecycle",
    params: { namespace: [], timestamp, data: { event } },
  }
}

function textDelta(text: string, timestamp: number) {
  return {
    type: "event",
    method: "messages",
    params: {
      namespace: [],
      timestamp,
      data: {
        event: "content-block-delta",
        id: "msg-1",
        delta: { type: "text-delta", text },
      },
    },
  }
}

afterEach(() => {
  clearPerfSpans()
  vi.restoreAllMocks()
})

describe("agent run span", () => {
  it("tracks a submitted run from send to completion", () => {
    vi.spyOn(Date, "now").mockReturnValue(10_000)
    const tracker = createRunTracker({ transport: "cloud" })

    tracker.submitted()
    recordRequestTiming({
      kind: "command",
      status: 200,
      ttfbMs: 88,
      serverTiming: [],
    })
    tracker.created()
    recordRequestTiming({
      kind: "stream_events",
      status: 200,
      ttfbMs: 120,
      serverTiming: [],
    })
    tracker.event(lifecycle("running", 9_900))
    tracker.event({
      type: "event",
      method: "messages",
      params: {
        namespace: [],
        timestamp: 9_950,
        data: { event: "message-start", role: "ai", id: "msg-1" },
      },
    })
    tracker.event(textDelta("", 9_960))
    tracker.event(textDelta("Hello", 9_970))
    tracker.event(textDelta(" world", 9_980))
    runTranscriptBuilt(2.5)
    runTranscriptCommitted(4)
    runTranscriptCommitted(9)
    expect(hasOpenRuns()).toBe(true)
    tracker.completed("success")

    const [span] = getPerfSpans()
    expect(span?.status).toBe("ended")
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "stream_open",
      "first_event",
      "first_token",
    ])
    expect(span?.attributes).toMatchObject({
      transport: "cloud",
      joined: false,
      command_ttfb_ms: 88,
      stream_ttfb_ms: 120,
      reason: "success",
      events: 5,
      text_deltas: 2,
      builds: 1,
      build_ms: 3,
      lag_avg_ms: 48,
      lag_max_ms: 100,
      commits: 2,
      commit_ms: 13,
      commit_max_ms: 9,
    })
    expect(hasOpenRuns()).toBe(false)
  })

  it("opens a joined span when a run starts that this client did not submit", () => {
    const tracker = createRunTracker({ transport: "local" })

    tracker.event(lifecycle("running", Date.now()))
    tracker.completed("interrupt")

    const [span] = getPerfSpans()
    expect(span?.attributes).toMatchObject({
      transport: "local",
      joined: true,
      reason: "interrupt",
    })
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "first_event",
    ])
  })

  it("abandons an in-flight run when the stream unmounts", () => {
    const tracker = createRunTracker({ transport: "cloud" })
    tracker.submitted()
    tracker.dispose()

    expect(getPerfSpans()[0]?.attributes["abandoned_reason"]).toBe("unmounted")
    expect(hasOpenRuns()).toBe(false)
  })

  it("ignores events with no run open and malformed events", () => {
    const tracker = createRunTracker({ transport: "cloud" })
    tracker.event(textDelta("stray", Date.now()))
    tracker.event(null)
    tracker.event({ method: "messages" })
    tracker.completed("success")

    expect(getPerfSpans()).toHaveLength(0)
  })
})
