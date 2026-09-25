/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useSubmitAgentMessage } from "./useSubmitAgentMessage"
import type { InfiniteData } from "@tanstack/react-query"
import type { AgentThread } from "@/features/agents/lib/types"
import type { ThreadsPage } from "@/features/agents/lib/api"
import { AgentsApiError } from "@/features/agents/lib/api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
} from "@/features/agents/lib/queries"

const source = {
  isRunning: false,
  startRun: vi.fn(() => Promise.resolve(undefined)),
}

vi.mock("@/features/agents/lib/threadSource/ThreadSourceProvider", () => ({
  useThreadSource: () => source,
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
  source.isRunning = false
  source.startRun.mockReset()
  source.startRun.mockResolvedValue(undefined)
})

describe("useSubmitAgentMessage", () => {
  it("offloads without adding a user message", async () => {
    const { client, result } = setup()
    await result.current.mutateAsync({ content: "/offload", images: [] })
    expect(source.startRun).toHaveBeenCalledWith({
      configurable: { offload_conversation: true },
    })
    expect(pendingMessages(client)).toBeUndefined()
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
  })

  it("marks the optimistic message failed with the reason when the start rejects", async () => {
    source.startRun.mockRejectedValueOnce(
      new AgentsApiError(503, "Service Unavailable")
    )
    const { client, result } = setup()

    await result.current.mutateAsync({ content: "try me", images: [] })

    await waitFor(() =>
      expect(pendingMessages(client)).toEqual([
        expect.objectContaining({
          content: "try me",
          status: "failed",
          error: "503 Service Unavailable",
        }),
      ])
    )
    expect(sidebarStatus(client)).toBe("error")
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
