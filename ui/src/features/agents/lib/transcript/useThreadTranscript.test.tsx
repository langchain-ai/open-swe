/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  agentThreadKeys,
  optimisticThread,
} from "@/features/agents/lib/queries"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { AgentsApiError } from "@/features/agents/lib/api"
import { fetchTranscript } from "./api"
import type { TranscriptEventHandlers } from "./api"
import type { StoredEvent } from "./types"
import { useThreadTranscript } from "./useThreadTranscript"

const stream = vi.hoisted(() => ({
  handlers: null as TranscriptEventHandlers | null,
}))
vi.mock("./api", () => ({
  fetchTranscript: vi.fn(async (id: string) => ({
    thread_id: id,
    version: 0,
    thread: { status: "idle" },
    turns: [],
    messages: [],
    tool_calls: [],
    notices: [],
    older_cursor: null,
  })),
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
  vi.mocked(fetchTranscript).mockReset()
  stream.handlers = null
  vi.useRealTimers()
})

it("recovers initial hydration after a temporary service outage", async () => {
  vi.useFakeTimers()
  vi.mocked(fetchTranscript).mockRejectedValueOnce(
    new AgentsApiError(503, "Service Unavailable")
  )
  const client = new QueryClient()
  const view = renderHook(() => useThreadTranscript("outage-recovery"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  await act(async () => {})
  expect(view.result.current.isHydrating).toBe(true)
  expect(view.result.current.error).toBeNull()
  expect(stream.handlers).toBeNull()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000)
    await view.result.current.hydration
  })
  expect(view.result.current.state?.threadId).toBe("outage-recovery")
  expect(view.result.current.isHydrating).toBe(false)
  expect(view.result.current.error).toBeNull()
  expect(stream.handlers).not.toBeNull()
})

it("cancels a pending hydration retry when the view unmounts", async () => {
  vi.useFakeTimers()
  vi.mocked(fetchTranscript).mockRejectedValueOnce(
    new AgentsApiError(503, "Service Unavailable")
  )
  const client = new QueryClient()
  const view = renderHook(() => useThreadTranscript("outage-unmounted"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  await act(async () => {})
  view.unmount()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(7_000)
  })
  expect(fetchTranscript).toHaveBeenCalledTimes(1)
  expect(stream.handlers).toBeNull()
})

it.each([403, 503])(
  "surfaces HTTP %s after only eligible retries",
  async (status) => {
    vi.useFakeTimers()
    const error = new AgentsApiError(status, "Unavailable")
    vi.mocked(fetchTranscript).mockRejectedValue(error)
    const client = new QueryClient()
    const view = renderHook(() => useThreadTranscript(`failure-${status}`), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(7_000)
      await expect(view.result.current.hydration).rejects.toBe(error)
    })
    expect(fetchTranscript).toHaveBeenCalledTimes(status === 503 ? 4 : 1)
    expect(view.result.current.error).toBe(error)
    expect(view.result.current.isHydrating).toBe(false)
    expect(stream.handlers).toBeNull()
    vi.mocked(fetchTranscript).mockReset()
  }
)

it("publishes first text immediately and flushes buffered text on completion", async () => {
  vi.useFakeTimers()
  const client = new QueryClient()
  const thread = optimisticThread("completion", { prompt: "Work" })
  const detailKey = agentThreadKeys.detail(thread.id)
  client.setQueryData(detailKey, thread)
  client.setQueryData(agentThreadKeys.pinned, [thread])
  const view = renderHook(() => useThreadTranscript("completion"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  await act(async () => {
    await view.result.current.hydration
  })
  act(() => stream.handlers?.onSynchronized())
  expect(client.getQueryState(detailKey)?.isInvalidated).toBe(true)
  client.setQueryData(detailKey, thread)
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
    expect(client.getQueryState(detailKey)?.isInvalidated).toBe(true)
    client.setQueryData(detailKey, thread)
    emit(append(2))
  })
  expect(view.result.current.state?.messages.message?.text).toBe("x")
  act(() => {
    emit(append(3))
    emit(append(4))
  })
  expect(view.result.current.state?.version).toBe(2)
  expect(client.getQueryState(detailKey)?.isInvalidated).toBe(false)
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
  expect(client.getQueryState(detailKey)?.isInvalidated).toBe(true)
})
