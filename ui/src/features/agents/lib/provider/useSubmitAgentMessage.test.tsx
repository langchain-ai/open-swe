/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useSubmitAgentMessage } from "./useSubmitAgentMessage"
import { notifyAgentRunCreated } from "./runAcceptance"
import type { SidebarThreads } from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import { AgentsApiError } from "@/features/agents/lib/api"
import { agentThreadKeys } from "@/features/agents/lib/queries"

const stream = {
  isLoading: false,
  submit: vi.fn<
    (
      input?: unknown,
      options?: { onError?: (error: unknown) => void }
    ) => Promise<void>
  >(async () => undefined),
}

vi.mock("@langchain/react", () => ({
  useStreamContext: () => stream,
}))

const queueMessage = vi.fn()

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: {
    queueMessage: (...args: Array<unknown>) => queueMessage(...args),
  },
  AgentsApiError: class extends Error {
    constructor(
      public readonly status: number,
      message: string
    ) {
      super(message)
    }
  },
}))

vi.mock("@/lib/plan", () => ({ rejectPlan: vi.fn() }))

const THREAD_ID = "thread-1"

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
  client.setQueryData<SidebarThreads>(
    agentThreadKeys.sidebar({
      activeLimit: 50,
      resolvedLimit: 20,
      includeAutomations: false,
    }),
    {
      active: { items: [thread], limit: 50, hasMore: false },
      resolved: { items: [], limit: 20, hasMore: false },
    }
  )
  const queuedCounts: Array<number> = []
  client.getQueryCache().subscribe(() => {
    const thread = client.getQueryData<AgentThread>(
      agentThreadKeys.detail(THREAD_ID)
    )
    queuedCounts.push(thread?.queuedMessages?.length ?? 0)
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
  return client.getQueriesData<SidebarThreads>({
    queryKey: ["agent-threads", "lists", "sidebar"],
  })[0]?.[1]?.active.items[0]?.status
}

beforeEach(() => {
  stream.isLoading = false
  stream.submit.mockClear()
  queueMessage.mockReset()
  queueMessage.mockResolvedValue(undefined)
})

describe("useSubmitAgentMessage", () => {
  it("shows a message immediately while probing an idle thread", async () => {
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
    expect(queuedMessages(client)?.[0]?.status).toBe("sending")
    acceptQueue()
    await pending
    expect(queuedMessages(client)?.[0]?.status).toBe("queued")
  })

  it("keeps the temporary message when the send starts a new run", async () => {
    queueMessage.mockRejectedValueOnce(new AgentsApiError(409, "no active run"))
    const { client, queuedCounts, result } = setup()

    const pending = result.current.mutateAsync({ content: "hi", images: [] })
    await waitFor(() => expect(stream.submit).toHaveBeenCalled())
    notifyAgentRunCreated(THREAD_ID)
    await pending

    expect(sidebarStatus(client)).toBe("running")
    expect(queuedMessages(client)).toHaveLength(1)
    expect(queuedCounts).toContain(1)
  })

  it("keeps the message once a run this client never joined accepts it", async () => {
    const { client, result } = setup()

    await result.current.mutateAsync({ content: "hi", images: [] })

    expect(queuedMessages(client)).toHaveLength(1)
    expect(queuedMessages(client)?.[0]?.status).toBe("queued")
    expect(stream.submit).not.toHaveBeenCalled()
  })

  it("rolls back a new-run message when startup fails", async () => {
    queueMessage.mockRejectedValueOnce(new AgentsApiError(409, "no active run"))
    stream.submit.mockImplementationOnce(async (_input, options) => {
      options?.onError?.(new Error("start failed"))
    })
    const { client, result } = setup()

    await expect(
      result.current.mutateAsync({ content: "hi", images: [] })
    ).rejects.toThrow("start failed")

    expect(queuedMessages(client)).toHaveLength(0)
  })

  it("rolls back the message when queueing fails", async () => {
    queueMessage.mockRejectedValueOnce(new Error("request failed"))
    const { client, result } = setup()

    await expect(
      result.current.mutateAsync({ content: "hi", images: [] })
    ).rejects.toThrow("request failed")

    expect(queuedMessages(client)).toHaveLength(0)
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
