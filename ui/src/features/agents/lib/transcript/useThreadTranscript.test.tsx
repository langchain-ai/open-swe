/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import type { TranscriptEventHandlers } from "./api"
import type { StoredEvent } from "./types"
import { useThreadTranscript } from "./useThreadTranscript"

const stream = vi.hoisted(() => ({
  handlers: null as TranscriptEventHandlers | null,
}))
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({}) }))
vi.mock("@/features/agents/lib/queries", () => ({}))
vi.mock("./api", () => ({
  fetchTranscript: async () => ({
    thread_id: "completion",
    version: 0,
    thread: { status: "idle" },
    turns: [],
    messages: [],
    tool_calls: [],
    notices: [],
    older_cursor: null,
  }),
  openTranscriptEvents: (
    _id: string,
    _after: number,
    handlers: TranscriptEventHandlers
  ) => {
    stream.handlers = handlers
    return { close: () => {} }
  },
}))
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

it("publishes first text immediately and flushes buffered text on completion", async () => {
  vi.useFakeTimers()
  const view = renderHook(() => useThreadTranscript("completion"))
  await act(async () => {
    await view.result.current.hydration
  })
  const emit = (event: StoredEvent) => stream.handlers?.onEvent(event)
  const envelope = { occurred_at: "2026-01-01T00:00:00Z" }
  const append = (version: number): StoredEvent => ({
    ...envelope,
    version,
    event_type: "message.appended",
    payload: {
      turn_id: "turn",
      message_id: "message",
      namespace: [],
      text: "x",
      reasoning: null,
    },
  })
  act(() => {
    emit({
      ...envelope,
      version: 1,
      event_type: "turn.started",
      payload: { turn_id: "turn" },
    })
    emit(append(2))
  })
  expect(view.result.current.state?.messages.message?.text).toBe("x")
  act(() => {
    emit(append(3))
    emit(append(4))
  })
  expect(view.result.current.state?.version).toBe(2)
  act(() =>
    emit({
      ...envelope,
      version: 5,
      event_type: "turn.completed",
      payload: { turn_id: "turn" },
    })
  )
  expect(view.result.current.state?.messages.message?.text).toBe("xxx")
  expect(view.result.current.isRunning).toBe(false)
})
