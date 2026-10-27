/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import type { TranscriptEventHandlers } from "./api"
import type { StoredEvent, TranscriptSnapshot } from "./types"
import { ThreadDeletedError, useThreadTranscript } from "./useThreadTranscript"

const mocks = vi.hoisted(() => ({
  streams: [] as Array<{ after: number; handlers: TranscriptEventHandlers }>,
}))
vi.mock("./api", () => ({
  fetchTranscript: async (threadId: string): Promise<TranscriptSnapshot> => ({
    thread_id: threadId,
    version: 0,
    thread: { status: "running" },
    turns: [
      {
        turn_id: "turn",
        run_id: "run",
        state: "running",
        requested_at: "2026-01-01T00:00:00Z",
        started_at: "2026-01-01T00:00:00Z",
        error: null,
      },
    ],
    messages: [],
    tool_calls: [],
    notices: [],
    older_cursor: null,
  }),
  openTranscriptEvents: (
    _id: string,
    after: number,
    callbacks: TranscriptEventHandlers
  ) => {
    mocks.streams.push({ after, handlers: callbacks })
    return { close: () => {} }
  },
  fetchOlderTurns: vi.fn(),
  attachmentUrl: () => "",
}))
vi.mock("@/features/agents/lib/queries", () => ({
  agentThreadKeys: { detail: () => [] },
  invalidateAgentThreadLists: () => {},
}))
vi.mock("@/lib/perf/threadLoad", () => ({
  threadHydrated: () => {},
  threadHydrationFailed: () => {},
  threadTranscriptBuilt: () => {},
}))
vi.mock("@/lib/perf/streaming", () => ({ runTranscriptBuilt: () => {} }))

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  )
}
function handlers() {
  const stream = mocks.streams.at(-1)
  if (!stream) throw new Error("No subscription")
  return stream.handlers
}
function append(version: number, text = "x"): StoredEvent {
  return {
    version,
    occurred_at: "2026-01-01T00:00:00Z",
    event_type: "message.appended",
    payload: {
      turn_id: "turn",
      message_id: "message",
      namespace: [],
      text,
      reasoning: null,
    },
  }
}
async function mount(threadId: string) {
  const view = renderHook(() => useThreadTranscript(threadId), { wrapper })
  await act(async () => {
    await view.result.current.hydration
  })
  return view
}
beforeEach(() => {
  vi.useFakeTimers()
  mocks.streams.length = 0
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

it("shows first text immediately, reduces every fragment, and flushes completion before a frame", async () => {
  const view = await mount("completion")
  act(() => handlers().onEvent(append(1)))
  expect(view.result.current.state?.messages.message?.text).toBe("x")
  act(() => {
    for (let i = 2; i <= 400; i++) handlers().onEvent(append(i))
  })
  expect(view.result.current.state?.version).toBe(1)
  act(() =>
    handlers().onEvent({
      version: 401,
      occurred_at: "2026-01-01T00:00:00Z",
      event_type: "turn.completed",
      payload: { turn_id: "turn" },
    })
  )
  expect(view.result.current.state?.version).toBe(401)
  expect(view.result.current.state?.messages.message?.text).toBe(
    "x".repeat(400)
  )
  expect(view.result.current.isRunning).toBe(false)
})
it("publishes fragments on the next frame and bounds publication when frames are suspended", async () => {
  const view = await mount("frames")
  act(() => {
    handlers().onEvent(append(1))
    handlers().onEvent(append(2))
  })
  act(() => vi.advanceTimersToNextFrame())
  expect(view.result.current.state?.version).toBe(2)
  const frame = vi
    .spyOn(window, "requestAnimationFrame")
    .mockImplementation(() => 123)
  act(() => handlers().onEvent(append(3)))
  act(() => vi.advanceTimersByTime(50))
  expect(view.result.current.state?.version).toBe(3)
  frame.mockRestore()
})
it("flushes the last replay fragment at synchronization", async () => {
  const view = await mount("synchronized")
  act(() => {
    handlers().onEvent(append(1))
    handlers().onEvent(append(2))
    handlers().onSynchronized()
  })
  expect(view.result.current.state?.version).toBe(2)
})
it("resumes from the last reduced version after a drop or an unmount before publication", async () => {
  const view = await mount("resume")
  act(() => {
    handlers().onEvent(append(1))
    handlers().onEvent(append(2))
    handlers().onError(new Error("drop"))
  })
  expect(view.result.current.state?.version).toBe(2)
  act(() => vi.advanceTimersByTime(30_000))
  expect(mocks.streams.at(-1)?.after).toBe(2)
  act(() => handlers().onEvent(append(3)))
  view.unmount()
  const remounted = await mount("resume")
  expect(remounted.result.current.state?.version).toBe(3)
  expect(mocks.streams.at(-1)?.after).toBe(3)
})
it("drops pending text on deletion and does not restore it from a late frame or the cache", async () => {
  const view = await mount("deleted")
  act(() => {
    handlers().onEvent(append(1))
    handlers().onEvent(append(2))
    handlers().onDeleted()
  })
  act(() => vi.advanceTimersByTime(100))
  expect(view.result.current.state).toBeNull()
  expect(view.result.current.error).toBeInstanceOf(ThreadDeletedError)
  view.unmount()
  const remounted = await mount("deleted")
  expect(remounted.result.current.state?.version).toBe(0)
})
