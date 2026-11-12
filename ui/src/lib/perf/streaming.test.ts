import { afterEach, describe, expect, it, vi } from "vitest"

import { recordRequestTiming } from "./fetchTiming"
import {
  RunTracker,
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

function messageStart(role: string, timestamp: number) {
  return {
    type: "event",
    method: "messages",
    params: {
      namespace: [],
      timestamp,
      data: { event: "message-start", role, id: "msg-1" },
    },
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

const THREAD_A = "0d5a5a4e-1b2c-4d3e-8f90-123456789abc"
const THREAD_B = "1e6b6b5f-2c3d-4e4f-9a01-23456789abcd"

afterEach(() => {
  clearPerfSpans()
  vi.restoreAllMocks()
})

describe("agent run span", () => {
  it("tracks a submitted run from send to completion", () => {
    vi.spyOn(Date, "now").mockReturnValue(10_000)
    const tracker = new RunTracker({ threadId: THREAD_A })

    tracker.submitted()
    recordRequestTiming({
      kind: "command",
      threadId: THREAD_A,
      status: 200,
      ttfbMs: 88,
      serverTiming: [],
    })
    tracker.created()
    recordRequestTiming({
      kind: "stream_events",
      threadId: THREAD_A,
      status: 200,
      ttfbMs: 120,
      serverTiming: [],
    })
    tracker.event(lifecycle("running", 9_900))
    tracker.event(messageStart("ai", 9_950))
    tracker.event(textDelta("", 9_960))
    tracker.event(textDelta("Hello", 9_970))
    tracker.event(textDelta(" world", 9_980))
    runTranscriptBuilt(THREAD_A, 2.5)
    runTranscriptCommitted(THREAD_A, 4)
    runTranscriptCommitted(THREAD_A, 9)
    tracker.completed("success")

    const [span] = getPerfSpans()
    expect(span?.status).toBe("ended")
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "stream_open",
      "first_event",
      "generation_start",
      "first_text",
    ])
    expect(span?.attributes).toMatchObject({
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
  })

  it("marks generation_start for a run that never streams assistant text", () => {
    vi.spyOn(Date, "now").mockReturnValue(10_000)
    const tracker = new RunTracker({ threadId: THREAD_A })

    tracker.submitted()
    tracker.event(lifecycle("running", 9_900))
    tracker.event(messageStart("ai", 9_950))
    tracker.completed("success")

    const [span] = getPerfSpans()
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "first_event",
      "generation_start",
    ])
    expect(span?.attributes).toMatchObject({ text_deltas: 0 })
  })

  it("ignores a non-assistant message start for generation_start", () => {
    vi.spyOn(Date, "now").mockReturnValue(10_000)
    const tracker = new RunTracker({ threadId: THREAD_A })

    tracker.submitted()
    tracker.event(messageStart("tool", 9_950))
    tracker.completed("success")

    const [span] = getPerfSpans()
    expect(span?.steps.map((step) => step.name)).toEqual(["first_event"])
  })

  it("marks first_text for prose that arrives only in its completed message", () => {
    const tracker = new RunTracker({ threadId: THREAD_A })

    tracker.transcriptEvent({ turnId: "turn-1", opensRun: true })
    tracker.transcriptEvent({ turnId: "turn-1", text: "message" })
    tracker.transcriptEvent({ turnId: "turn-1", ends: "success" })

    const [span] = getPerfSpans()
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "first_event",
      "first_text",
    ])
    expect(span?.attributes).toMatchObject({ text_deltas: 0 })
  })

  it.each([
    {
      case: "a message queued behind the running turn",
      requested: ["turn-c"],
      own: "turn-c",
    },
    {
      case: "a message queued behind another queued message",
      requested: ["turn-b", "turn-c"],
      own: "turn-c",
    },
    {
      case: "a message that steers the running turn",
      requested: [],
      own: "turn-a",
    },
  ])("times only the turn that records $case", ({ requested, own }) => {
    const tracker = new RunTracker({ threadId: THREAD_A })
    tracker.transcriptEvent({ turnId: "turn-a", opensRun: true })
    tracker.submitted({ messageId: "msg-c", runningTurnId: "turn-a" })

    for (const turnId of requested)
      tracker.transcriptEvent({
        turnId,
        humanMessageId: turnId === own ? "msg-c" : `msg-${turnId}`,
      })
    if (own === "turn-a")
      tracker.transcriptEvent({ turnId: "turn-a", humanMessageId: "msg-c" })
    else {
      tracker.transcriptEvent({ turnId: "turn-a", text: "message" })
      tracker.transcriptEvent({ turnId: "turn-a", ends: "success" })
      tracker.transcriptEvent({ turnId: "turn-b", opensRun: true })
      tracker.transcriptEvent({ turnId: "turn-b", text: "message" })
      tracker.transcriptEvent({ turnId: "turn-b", ends: "success" })
      tracker.transcriptEvent({ turnId: own, opensRun: true })
    }
    tracker.transcriptEvent({ turnId: own, text: "delta" })
    tracker.transcriptEvent({ turnId: own, ends: "success" })

    const submitted = getPerfSpans().find(
      (span) => span.attributes["joined"] === false
    )
    expect(submitted?.status).toBe("ended")
    expect(submitted?.steps.map((step) => step.name)).toEqual([
      "first_event",
      "first_text",
    ])
    expect(submitted?.attributes).toMatchObject({ text_deltas: 1 })
  })

  it("opens a joined span when a run starts that this client did not submit", () => {
    const tracker = new RunTracker({ threadId: THREAD_A })

    tracker.event(lifecycle("running", Date.now()))
    tracker.completed("interrupt")

    const [span] = getPerfSpans()
    expect(span?.attributes).toMatchObject({
      joined: true,
      reason: "interrupt",
    })
    expect(span?.steps.map((step) => step.name)).toEqual([
      "accepted",
      "first_event",
    ])
  })

  it("keeps request and transcript timings on the stream that owns the thread", () => {
    const runA = new RunTracker({ threadId: THREAD_A })
    const runB = new RunTracker({ threadId: null })
    runA.submitted()
    runB.bindThread(THREAD_B.toUpperCase())
    runB.submitted()

    recordRequestTiming({
      kind: "command",
      threadId: THREAD_B,
      status: 200,
      ttfbMs: 50,
      serverTiming: [],
    })
    recordRequestTiming({
      kind: "stream_events",
      threadId: THREAD_B,
      status: 200,
      ttfbMs: 70,
      serverTiming: [],
    })
    runTranscriptBuilt(THREAD_B, 5)
    runTranscriptCommitted(THREAD_B, 8)
    runA.completed("success")
    runB.completed("success")

    const [spanA, spanB] = getPerfSpans()
    expect(spanA?.attributes).not.toHaveProperty("command_ttfb_ms")
    expect(spanA?.steps.map((step) => step.name)).toEqual([])
    expect(spanA?.attributes).toMatchObject({ builds: 0, build_ms: 0 })
    expect(spanA?.attributes).not.toHaveProperty("commits")
    expect(spanB?.attributes).toMatchObject({
      command_ttfb_ms: 50,
      stream_ttfb_ms: 70,
      builds: 1,
      build_ms: 5,
      commits: 1,
    })
  })
})
