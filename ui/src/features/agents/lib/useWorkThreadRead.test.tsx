/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import { agentThreadKeys, optimisticThread, WorkThreadCache } from "./queries"
import { useWorkThreadRead } from "./useWorkThreadRead"
import { AgentThreadStreamBoundary } from "./provider/useIsInAgentThreadStream"
import type { AgentThread } from "./types"
import type { TranscriptThreadSource } from "./threadSource/types"
import type { ThreadsPage } from "./api"

class ReadHarness {
  readonly client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  readonly thread: AgentThread = {
    ...optimisticThread("work-read", { prompt: "Answer" }),
    experience: "agent_workspace",
    status: "finished",
    viewed: false,
    latestRunId: "run-1",
  }
  readonly source: TranscriptThreadSource = {
    kind: "transcript",
    threadId: this.thread.id,
    settledRunId: "run-1",
    messages: [],
    queued: [],
    isRunning: false,
    isHydrating: false,
    hydration: Promise.resolve(),
    error: null,
    isOffloading: false,
    routed: null,
    connection: { status: "live" },
    contextUsage: null,
    startRun: async () => {},
    stop: async () => true,
    hasOlder: false,
    isLoadingOlder: false,
    loadOlder: () => {},
    subagentToolCalls: () => [],
    subagentMessages: () => [],
    subagentTask: () => null,
    workspaceStale: null,
  }

  constructor() {
    this.client.setQueryData(
      agentThreadKeys.detail(this.thread.id),
      this.thread
    )
    this.client.setQueryData(agentThreadKeys.pinned, [this.thread])
    this.client.setQueryData(agentThreadKeys.page({}), {
      items: [this.thread],
      offset: 0,
      limit: 10,
      hasMore: false,
    })
  }

  mount() {
    return renderHook(
      ({ thread, source }) => useWorkThreadRead(thread, source),
      {
        initialProps: { thread: this.thread, source: this.source },
        wrapper: ({ children }) => (
          <QueryClientProvider client={this.client}>
            <AgentThreadStreamBoundary>{children}</AgentThreadStreamBoundary>
          </QueryClientProvider>
        ),
      }
    )
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it("acknowledges only a hydrated matching answer in a visible focused document", async () => {
  vi.useFakeTimers()
  const harness = new ReadHarness()
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(false)
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("hidden")
  const acknowledge = vi
    .spyOn(agentsApi, "markThreadViewed")
    .mockResolvedValue({ runId: "run-1", viewed: true, viewedAt: 123 })
  const view = harness.mount()
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
  visibility.mockReturnValue("visible")
  act(() => document.dispatchEvent(new Event("visibilitychange")))
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
  focus.mockReturnValue(true)
  act(() => window.dispatchEvent(new Event("focus")))
  view.rerender({
    thread: harness.thread,
    source: { ...harness.source, isHydrating: true },
  })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
  view.rerender({
    thread: { ...harness.thread, latestRunId: "run-2" },
    source: harness.source,
  })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
  view.rerender({ thread: harness.thread, source: harness.source })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).toHaveBeenCalledExactlyOnceWith(
    harness.thread.id,
    "run-1"
  )
  expect(
    harness.client.getQueryData<Array<AgentThread>>(agentThreadKeys.pinned)?.[0]
      ?.viewed
  ).toBe(true)
  expect(
    harness.client.getQueryData<ThreadsPage>(agentThreadKeys.page({}))?.items[0]
      ?.viewed
  ).toBe(true)
})

it("does not acknowledge Coding or an answer blurred before its display frame", async () => {
  vi.useFakeTimers()
  const harness = new ReadHarness()
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(true)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  const acknowledge = vi.spyOn(agentsApi, "markThreadViewed")
  const view = harness.mount()
  focus.mockReturnValue(false)
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
  focus.mockReturnValue(true)
  act(() => window.dispatchEvent(new Event("focus")))
  view.rerender({
    thread: { ...harness.thread, experience: "coding" },
    source: harness.source,
  })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
})

it("keeps a newer run unread when an older acknowledgement returns and reconciles mismatches", async () => {
  const harness = new ReadHarness()
  let resolve!: (receipt: {
    runId: string | null
    viewed: boolean
    viewedAt: number | null
  }) => void
  vi.spyOn(agentsApi, "markThreadViewed").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      })
  )
  const cache = new WorkThreadCache(harness.client, harness.thread.id)
  const pending = cache.acknowledge("run-1")
  const newer = { ...harness.thread, latestRunId: "run-2", viewed: false }
  harness.client.setQueryData(agentThreadKeys.detail(newer.id), newer)
  harness.client.setQueryData(agentThreadKeys.pinned, [newer])
  resolve({ runId: "run-1", viewed: true, viewedAt: 123 })
  await pending
  expect(
    harness.client.getQueryData<AgentThread>(agentThreadKeys.detail(newer.id))
      ?.viewed
  ).toBe(false)
  expect(
    harness.client.getQueryData<Array<AgentThread>>(agentThreadKeys.pinned)?.[0]
      ?.viewed
  ).toBe(false)
  const mismatch = cache.acknowledge("run-1")
  resolve({ runId: "run-2", viewed: false, viewedAt: null })
  await mismatch
  expect(
    harness.client.getQueryState(agentThreadKeys.detail(newer.id))
      ?.isInvalidated
  ).toBe(true)
  expect(
    harness.client.getQueryState(agentThreadKeys.pinned)?.isInvalidated
  ).toBe(true)
})

it("retries transient failures only while the matching result stays active and foregrounded", async () => {
  vi.useFakeTimers()
  const harness = new ReadHarness()
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(true)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  vi.spyOn(console, "warn").mockImplementation(() => {})
  const acknowledge = vi
    .spyOn(agentsApi, "markThreadViewed")
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ runId: "run-1", viewed: true, viewedAt: 123 })
  const view = harness.mount()
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).toHaveBeenCalledTimes(1)
  focus.mockReturnValue(false)
  await act(() => vi.advanceTimersByTimeAsync(2000))
  expect(acknowledge).toHaveBeenCalledTimes(1)
  focus.mockReturnValue(true)
  act(() => window.dispatchEvent(new Event("online")))
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).toHaveBeenCalledTimes(2)
  view.unmount()
  await act(() => vi.advanceTimersByTimeAsync(5000))
  expect(acknowledge).toHaveBeenCalledTimes(2)
})

it("bounds foreground retry attempts after a transient acknowledgement failure", async () => {
  vi.useFakeTimers()
  const harness = new ReadHarness()
  vi.spyOn(document, "hasFocus").mockReturnValue(true)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  vi.spyOn(console, "warn").mockImplementation(() => {})
  const acknowledge = vi
    .spyOn(agentsApi, "markThreadViewed")
    .mockRejectedValue(new Error("offline"))
  harness.mount()
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(1000))
  expect(acknowledge).toHaveBeenCalledTimes(2)
  await act(() => vi.advanceTimersByTimeAsync(2000))
  expect(acknowledge).toHaveBeenCalledTimes(3)
  await act(() => vi.advanceTimersByTimeAsync(10_000))
  expect(acknowledge).toHaveBeenCalledTimes(3)
})

it("never acknowledges a mounted inactive Work page", async () => {
  vi.useFakeTimers()
  const harness = new ReadHarness()
  vi.spyOn(document, "hasFocus").mockReturnValue(true)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  const acknowledge = vi.spyOn(agentsApi, "markThreadViewed")
  renderHook(() => useWorkThreadRead(harness.thread, harness.source), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={harness.client}>
        <AgentThreadStreamBoundary active={false}>
          {children}
        </AgentThreadStreamBoundary>
      </QueryClientProvider>
    ),
  })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(acknowledge).not.toHaveBeenCalled()
})
