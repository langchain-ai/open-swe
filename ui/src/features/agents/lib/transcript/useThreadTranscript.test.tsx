/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  agentThreadKeys,
  optimisticThread,
} from "@/features/agents/lib/queries"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import type { TranscriptEventHandlers } from "./api"
import type { StoredEvent } from "./types"
import { useThreadTranscript } from "./useThreadTranscript"
import { useTranscriptSource } from "@/features/agents/lib/threadSource/useTranscriptSource"
import { useWorkThreadRead } from "@/features/agents/lib/useWorkThreadRead"
import { agentsApi } from "@/features/agents/lib/api"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"

const stream = vi.hoisted(() => ({
  handlers: null as TranscriptEventHandlers | null,
}))
vi.mock("./api", () => ({
  fetchTranscript: async (id: string) => ({
    thread_id: id,
    version: 0,
    thread: { status: "idle" },
    turns:
      id === "failed-no-output"
        ? [
            {
              turn_id: "failed-turn",
              run_id: "failed-run",
              state: "failed",
              requested_at: "2026-01-01T00:00:00Z",
              started_at: null,
              error: "Run failed",
            },
          ]
        : [],
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
  const client = new QueryClient()
  const thread = {
    ...optimisticThread("completion", { prompt: "Work" }),
    experience: "agent_workspace" as const,
  }
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
  client.setQueryData(detailKey, { ...thread, experience: "coding" })
  act(() => stream.handlers?.onSynchronized())
  expect(client.getQueryState(detailKey)?.isInvalidated).toBe(false)
})

it("acknowledges a hydrated failed run with no AI output instead of stranding its unread marker", async () => {
  vi.useFakeTimers()
  vi.spyOn(document, "hasFocus").mockReturnValue(true)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
  const acknowledge = vi
    .spyOn(agentsApi, "markThreadViewed")
    .mockResolvedValue({ runId: "failed-run", viewed: true, viewedAt: 123 })
  const client = new QueryClient()
  const thread = {
    ...optimisticThread("failed-no-output", { prompt: "Work" }),
    experience: "agent_workspace" as const,
    status: "error" as const,
    latestRunId: "failed-run",
    viewed: false,
  }
  client.setQueryData(agentThreadKeys.detail(thread.id), thread)
  const view = renderHook(
    () => {
      const source = useTranscriptSource(thread.id)
      useWorkThreadRead(thread, source)
      return source
    },
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <AgentThreadStreamBoundary>{children}</AgentThreadStreamBoundary>
        </QueryClientProvider>
      ),
    }
  )
  await act(async () => {
    await view.result.current.hydration
  })
  await act(() => vi.advanceTimersByTimeAsync(20))
  expect(view.result.current.messages).toEqual([])
  expect(view.result.current.settledRunId).toBe("failed-run")
  expect(acknowledge).toHaveBeenCalledExactlyOnceWith(thread.id, "failed-run")
  vi.restoreAllMocks()
})
