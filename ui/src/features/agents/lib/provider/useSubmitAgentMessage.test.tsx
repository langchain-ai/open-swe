/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useSubmitAgentMessage } from "./useSubmitAgentMessage"
import type { InfiniteData } from "@tanstack/react-query"
import type { AgentThread } from "@/features/agents/lib/types"
import type { ThreadsPage } from "@/features/agents/lib/api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
} from "@/features/agents/lib/queries"

const stream = {
  isLoading: false,
  submit: vi.fn(
    (
      _input: unknown,
      _options?: { onError?: (error: unknown) => void }
    ): Promise<undefined> => Promise.resolve(undefined)
  ),
}

vi.mock("@/features/agents/lib/stream/AgentStreamProvider", () => ({
  useAgentStream: () => stream,
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
  stream.isLoading = false
  stream.submit.mockReset()
  stream.submit.mockImplementation(() => Promise.resolve(undefined))
})

describe("useSubmitAgentMessage", () => {
  it("offloads without adding a user message", async () => {
    const { client, result } = setup()
    await result.current.mutateAsync({ content: "/offload", images: [] })
    expect(stream.submit).toHaveBeenCalledWith(
      {},
      { config: { configurable: { offload_conversation: true } } }
    )
    expect(pendingMessages(client)).toBeUndefined()
  })

  it("rejects offloading during a live run instead of submitting it", async () => {
    stream.isLoading = true
    const { result } = setup()
    await expect(
      result.current.mutateAsync({ content: "/offload" })
    ).rejects.toThrow("Wait for the current run")
    expect(stream.submit).not.toHaveBeenCalled()
  })

  it("shows an optimistic pending message immediately and submits with the enqueue strategy regardless of whether a run is active", async () => {
    const { client, result } = setup()

    const pending = result.current.mutateAsync({ content: "hi", images: [] })
    await waitFor(() => expect(pendingMessages(client)).toHaveLength(1))
    const optimisticId = pendingMessages(client)?.[0]?.id
    expect(pendingMessages(client)?.[0]).toMatchObject({
      content: "hi",
      status: "sending",
    })

    await pending

    expect(stream.submit).toHaveBeenCalledWith(
      {
        messages: [
          expect.objectContaining({ id: optimisticId, type: "human" }),
        ],
      },
      expect.objectContaining({ multitaskStrategy: "enqueue" })
    )
    await waitFor(() => expect(sidebarStatus(client)).toBe("running"))
  })

  it("marks the message failed via onError when the queued create() fails", async () => {
    stream.submit.mockImplementationOnce((_input, options) => {
      options?.onError?.(new Error("create failed"))
      return Promise.resolve(undefined)
    })
    const { client, result } = setup()

    await result.current.mutateAsync({ content: "try me", images: [] })

    await waitFor(() =>
      expect(pendingMessages(client)).toEqual([
        expect.objectContaining({ content: "try me", status: "failed" }),
      ])
    )
  })
})
