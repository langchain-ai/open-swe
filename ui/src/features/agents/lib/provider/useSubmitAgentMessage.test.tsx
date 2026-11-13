/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useSubmitAgentMessage } from "./useSubmitAgentMessage"
import type { InfiniteData } from "@tanstack/react-query"
import type { AgentThread } from "@/features/agents/lib/types"
import type { ThreadRunInput } from "@/features/agents/lib/threadSource/types"
import type { ThreadsPage } from "@/features/agents/lib/api"
import { AgentsApiError } from "@/features/agents/lib/api"
import { reportError } from "@/lib/errorReporting"
import { productAnalytics } from "@/lib/analytics"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
} from "@/features/agents/lib/queries"

const source = {
  isRunning: false,
  startRun: vi.fn((_input: ThreadRunInput) => Promise.resolve(undefined)),
}

vi.mock("@/features/agents/lib/threadSource/ThreadSourceProvider", () => ({
  useThreadSource: () => source,
}))
vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
const outcome = vi.hoisted(() => ({ accepted: vi.fn(), failed: vi.fn() }))
vi.mock("@/lib/analytics", () => ({
  productAnalytics: { track: vi.fn(), beginAction: vi.fn(() => outcome) },
}))

const THREAD_ID = "thread-1"
const SIDEBAR_PARAMS = {
  limit: SIDEBAR_PAGE_SIZE,
  resolved: false,
  scope: "interactive" as const,
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  const thread = {
    id: THREAD_ID,
    status: "idle",
    messages: [],
  } as unknown as AgentThread
  client.setQueryData(agentThreadKeys.detail(THREAD_ID), thread)
  client.setQueryData<InfiniteData<ThreadsPage>>(
    agentThreadKeys.infinitePages(SIDEBAR_PARAMS),
    {
      pages: [
        {
          items: [thread],
          limit: SIDEBAR_PAGE_SIZE,
          offset: 0,
          hasMore: false,
        },
      ],
      pageParams: [0],
    }
  )
  const { result } = renderHook(() => useSubmitAgentMessage(THREAD_ID), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  return { client, result }
}

function pendingMessages(client: QueryClient) {
  return client.getQueryData<AgentThread>(agentThreadKeys.detail(THREAD_ID))
    ?.pendingMessages
}

function sidebarStatus(client: QueryClient) {
  return client.getQueryData<InfiniteData<ThreadsPage>>(
    agentThreadKeys.infinitePages(SIDEBAR_PARAMS)
  )?.pages[0]?.items[0]?.status
}

beforeEach(() => {
  outcome.accepted.mockClear()
  outcome.failed.mockClear()
  source.isRunning = false
  source.startRun.mockReset()
  source.startRun.mockResolvedValue(undefined)
  vi.mocked(productAnalytics.track).mockClear()
  vi.mocked(reportError).mockReset()
  vi.mocked(reportError).mockReturnValue("err_send-failed")
})

describe("useSubmitAgentMessage", () => {
  it.each([
    { running: false, enqueue: false, mode: "new_turn" },
    { running: true, enqueue: false, mode: "steer" },
    { running: true, enqueue: true, mode: "queue" },
  ])(
    "tracks submission mode $mode without message content",
    async ({ running, enqueue, mode }) => {
      source.isRunning = running
      const { result } = setup()
      await result.current.mutateAsync({
        content: "private prompt",
        images: [],
        enqueue,
      })
      expect(productAnalytics.track).toHaveBeenCalledExactlyOnceWith(
        "agents_message_submitted",
        {
          mode,
          has_attachments: false,
        },
        undefined
      )
    }
  )

  it.each([
    { model: null, changed: false },
    { model: null, changed: true },
    { model: "gpt-5.5", changed: false },
    { model: "gpt-5.5", changed: true },
  ])(
    "sends a picker action separately from the shown model ($model, changed=$changed)",
    async ({ model, changed }) => {
      const { result } = setup()
      await result.current.mutateAsync({
        content: "Fix the typo",
        model_id: model,
        effort: model ? "high" : null,
        model_selection_changed: changed,
      })
      expect(source.startRun).toHaveBeenCalledWith(
        expect.objectContaining({
          configurable: {
            ...(model
              ? {
                  agent_model_id: model,
                  agent_effort: "high",
                  model_selection: "explicit",
                }
              : { model_selection: "auto" }),
            ...(changed ? { model_selection_changed: true } : {}),
          },
        })
      )
    }
  )

  it.each([false, true])(
    "inherits the thread model with a hidden picker, unless an Auto action is pending (changed=%s)",
    async (changed) => {
      const { result } = setup()
      await result.current.mutateAsync({
        content: "Continue",
        inherit_model_selection: true,
        model_selection_changed: changed,
      })
      expect(source.startRun).toHaveBeenCalledWith(
        expect.objectContaining({
          configurable: changed
            ? { model_selection: "auto", model_selection_changed: true }
            : {},
        })
      )
    }
  )

  it("offloads without adding a user message", async () => {
    const { client, result } = setup()
    await result.current.mutateAsync({ content: "/offload", images: [] })
    expect(source.startRun).toHaveBeenCalledWith({
      configurable: { offload_conversation: true },
      onAccepted: expect.any(Function),
      onRejected: expect.any(Function),
    })
    expect(pendingMessages(client)).toBeUndefined()
    expect(productAnalytics.track).not.toHaveBeenCalled()
  })

  it("rejects offloading during a live run", async () => {
    source.isRunning = true
    const { result } = setup()
    await expect(
      result.current.mutateAsync({ content: "/offload" })
    ).rejects.toThrow("Wait for the current run")
    expect(source.startRun).not.toHaveBeenCalled()
  })

  it("shows the message as sending without waiting for the run to finish", async () => {
    // The SDK stream's start settles only when the run ends.
    source.startRun.mockImplementationOnce(
      () => new Promise<undefined>(() => {})
    )
    const { client, result } = setup()

    await result.current.mutateAsync({ content: "hi", images: [] })

    expect(pendingMessages(client)).toHaveLength(1)
    const optimisticId = pendingMessages(client)?.[0]?.id
    expect(pendingMessages(client)?.[0]).toMatchObject({
      content: "hi",
      status: "sending",
    })
    expect(source.startRun).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.objectContaining({ id: optimisticId, text: "hi" }),
      })
    )
    expect(sidebarStatus(client)).toBe("running")
    expect(outcome.accepted).not.toHaveBeenCalled()
    source.startRun.mock.calls[0]?.[0].onAccepted?.()
    expect(outcome.accepted).toHaveBeenCalledOnce()
  })

  it("marks the optimistic message failed with the reason when the start rejects", async () => {
    source.startRun.mockRejectedValueOnce(
      new AgentsApiError(503, "Service Unavailable")
    )
    const { client, result } = setup()

    const onStartError = vi.fn()
    await result.current.mutateAsync({
      content: "try me",
      images: [],
      onStartError,
    })

    await waitFor(() =>
      expect(pendingMessages(client)).toEqual([
        expect.objectContaining({
          content: "try me",
          status: "failed",
          error: "503 Service Unavailable · ID err_send-failed",
        }),
      ])
    )
    expect(sidebarStatus(client)).toBe("error")
    expect(onStartError).toHaveBeenCalledOnce()
    expect(outcome.failed).toHaveBeenCalledWith(expect.any(AgentsApiError))
    expect(reportError).toHaveBeenCalledWith({
      title: "Couldn't send message",
      error: expect.any(AgentsApiError),
    })
  })

  it("clears queued on a failed enqueue so the failed row leaves the queue", async () => {
    source.startRun.mockRejectedValueOnce(
      new AgentsApiError(503, "Service Unavailable")
    )
    const { client, result } = setup()

    await result.current.mutateAsync({
      content: "try me",
      images: [],
      enqueue: true,
    })

    await waitFor(() =>
      expect(pendingMessages(client)).toEqual([
        expect.objectContaining({
          content: "try me",
          status: "failed",
          queued: false,
        }),
      ])
    )
  })
})
