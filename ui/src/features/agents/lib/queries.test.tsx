/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  agentThreadKeys,
  useSetAgentThreadFocusState,
  useThreadsPage,
} from "./queries"
import type { ThreadsPage, ThreadsPageParams } from "./api"
import type { AgentThread } from "./types"

const params: ThreadsPageParams = {
  limit: 100,
  offset: 0,
  scope: "interactive",
}

const page: ThreadsPage = {
  items: [],
  limit: 100,
  offset: 0,
  hasMore: false,
}

const clients: Array<QueryClient> = []

afterEach(() => {
  vi.useRealTimers()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

describe("useThreadsPage", () => {
  it("keeps cached threads visible while stale data revalidates", async () => {
    const never = new Promise<ThreadsPage>(() => {})
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockResolvedValueOnce(page)
      .mockReturnValueOnce(never)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    clients.push(client)
    const seen: Array<ReturnType<typeof useThreadsPage>> = []

    function Probe() {
      seen.push(useThreadsPage(params, { staleWhileRevalidate: true }))
      return null
    }

    const first = render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )
    await waitFor(() => expect(seen.at(-1)?.data).toBe(page))
    vi.useFakeTimers()
    first.unmount()
    vi.advanceTimersByTime(24 * 60 * 60_000)
    expect(client.getQueryData(agentThreadKeys.page(params))).toBe(page)
    vi.useRealTimers()

    await client.invalidateQueries({ queryKey: agentThreadKeys.page(params) })
    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(2))
    expect(seen.at(-1)?.data).toBe(page)
    expect(seen.at(-1)?.isLoading).toBe(false)
    expect(seen.at(-1)?.isFetching).toBe(true)
  })
})

describe("useSetAgentThreadFocusState", () => {
  it("moves the cached thread optimistically and reconciles the response", async () => {
    const thread: AgentThread = {
      id: "thread-1",
      title: "Move me",
      repo: "open-swe",
      repoFullName: "langchain-ai/open-swe",
      branch: "main",
      model: "default",
      status: "idle",
      viewed: true,
      createdAt: 1,
      updatedAt: 1,
      messages: [],
    }
    const serverThread: AgentThread = {
      ...thread,
      boardFocusState: "progress",
      resolved: false,
      resolvedAt: null,
    }
    let resolveRequest: ((value: AgentThread) => void) | undefined
    vi.spyOn(agentsApi, "setThreadFocusState").mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve
      })
    )
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    clients.push(client)
    client.setQueryData(agentThreadKeys.page(params), {
      ...page,
      items: [thread],
    })

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useSetAgentThreadFocusState(), {
      wrapper,
    })

    act(() => {
      result.current.mutate({
        threadId: thread.id,
        focusState: "progress",
      })
    })

    await waitFor(() =>
      expect(
        client.getQueryData<ThreadsPage>(agentThreadKeys.page(params))?.items[0]
          ?.boardFocusState
      ).toBe("progress")
    )
    expect(result.current.isPending).toBe(true)

    resolveRequest?.(serverThread)

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(
      client.getQueryData<ThreadsPage>(agentThreadKeys.page(params))?.items[0]
    ).toEqual(serverThread)
  })

  it("rolls back only the failed thread", async () => {
    const thread = {
      id: "thread-1",
      title: "Move me",
      repo: "open-swe",
      repoFullName: "langchain-ai/open-swe",
      branch: "main",
      model: "default",
      status: "idle",
      viewed: true,
      createdAt: 1,
      updatedAt: 1,
      messages: [],
    } satisfies AgentThread
    const other = { ...thread, id: "thread-2", title: "Keep me" }
    let rejectRequest: ((reason: Error) => void) | undefined
    vi.spyOn(agentsApi, "setThreadFocusState").mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectRequest = reject
      })
    )
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    clients.push(client)
    client.setQueryData(agentThreadKeys.page(params), {
      ...page,
      items: [thread, other],
    })
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useSetAgentThreadFocusState(), {
      wrapper,
    })

    act(() => {
      result.current.mutate({ threadId: thread.id, focusState: "progress" })
    })
    await waitFor(() => expect(result.current.isPending).toBe(true))
    client.setQueryData<ThreadsPage>(
      agentThreadKeys.page(params),
      (current) => ({
        ...current!,
        items: current!.items.map((item) =>
          item.id === other.id ? { ...item, boardFocusState: "ready" } : item
        ),
      })
    )

    rejectRequest?.(new Error("failed"))

    await waitFor(() => expect(result.current.isError).toBe(true))
    const items = client.getQueryData<ThreadsPage>(
      agentThreadKeys.page(params)
    )?.items
    expect(items?.find((item) => item.id === thread.id)).toEqual(thread)
    expect(items?.find((item) => item.id === other.id)?.boardFocusState).toBe(
      "ready"
    )
  })
})
