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

const stream = {
  isLoading: false,
  submit: vi.fn(() => Promise.resolve(undefined)),
}

vi.mock("@/features/agents/lib/AgentThreadStreamProvider", () => ({
  useAgentThreadRuntime: () => stream,
}))

const queueMessage = vi.fn()

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: { queueMessage: () => queueMessage() },
  AgentsApiError: class extends Error {
    constructor(
      public readonly status: number,
      message: string
    ) {
      super(message)
    }
  },
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
  const queuedCounts: Array<number> = []
  client.getQueryCache().subscribe(() => {
    const current = client.getQueryData<AgentThread>(
      agentThreadKeys.detail(THREAD_ID)
    )
    queuedCounts.push(current?.queuedMessages?.length ?? 0)
  })
  const { result } = renderHook(() => useSubmitAgentMessage(THREAD_ID), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  return { client, queuedCounts, result }
}

function queuedMessages(client: QueryClient) {
  return client.getQueryData<AgentThread>(agentThreadKeys.detail(THREAD_ID))
    ?.queuedMessages
}

function sidebarStatus(client: QueryClient) {
  return client.getQueryData<InfiniteData<ThreadsPage>>(
    agentThreadKeys.infinitePages(SIDEBAR_PARAMS)
  )?.pages[0]?.items[0]?.status
}

beforeEach(() => {
  stream.isLoading = false
  stream.submit.mockClear()
  queueMessage.mockReset()
  queueMessage.mockResolvedValue(undefined)
})

describe("useSubmitAgentMessage", () => {
  it("never flashes a queued bubble when the send starts a new run", async () => {
    queueMessage.mockRejectedValueOnce(new AgentsApiError(409, "no active run"))
    const { client, queuedCounts, result } = setup()

    await result.current.mutateAsync({ content: "hi", images: [] })

    await waitFor(() => expect(stream.submit).toHaveBeenCalled())
    expect(sidebarStatus(client)).toBe("running")
    expect(queuedCounts.every((count) => count === 0)).toBe(true)
  })

  it("shows the queued bubble once a run this client never joined accepts it", async () => {
    const { client, result } = setup()

    await result.current.mutateAsync({ content: "hi", images: [] })

    expect(queuedMessages(client)).toHaveLength(1)
    expect(stream.submit).not.toHaveBeenCalled()
  })

  it("shows the queued bubble immediately while this client streams", async () => {
    stream.isLoading = true
    let acceptQueue: () => void = () => {}
    queueMessage.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          acceptQueue = () => resolve(undefined)
        })
    )
    const { client, result } = setup()

    const pending = result.current.mutateAsync({ content: "hi", images: [] })
    await waitFor(() => expect(queuedMessages(client)).toHaveLength(1))
    acceptQueue()
    await pending
  })
})
