/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
  useAgentThreadWorkingTreeDiff,
  useResolveAgentThread,
  useSidebarThreads,
  useThreadsPage,
} from "./queries"
import type { ThreadTurnDiff, ThreadsPageParams } from "./api"
import type { AgentThread } from "./types"

const params: ThreadsPageParams = {
  limit: 100,
  scope: "interactive",
}

const clients: Array<QueryClient> = []

afterEach(() => {
  vi.useRealTimers()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function testClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return client
}

describe("useAgentThreadWorkingTreeDiff", () => {
  const diff: ThreadTurnDiff = {
    status: "ready",
    truncated: false,
    summary: { files: 0, additions: 0, deletions: 0 },
    files: [],
  }

  function Probe({
    running,
    enabled = true,
  }: {
    running: boolean
    enabled?: boolean
  }) {
    useAgentThreadWorkingTreeDiff("thread-1", enabled, running)
    return null
  }

  function renderProbe(running: boolean, enabled = true) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    clients.push(client)
    return render(
      <QueryClientProvider client={client}>
        <Probe running={running} enabled={enabled} />
      </QueryClientProvider>
    )
  }

  it("keeps polling ready cloud diffs while the run is active", async () => {
    vi.useFakeTimers()
    const getDiff = vi
      .spyOn(agentsApi, "getThreadWorkingTreeDiff")
      .mockResolvedValue(diff)

    renderProbe(true)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(3000)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(2))
  })

  it("refreshes immediately and twice after a running cloud diff finishes", async () => {
    vi.useFakeTimers()
    const getDiff = vi
      .spyOn(agentsApi, "getThreadWorkingTreeDiff")
      .mockResolvedValue(diff)
    const view = renderProbe(true)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(1))

    view.rerender(
      <QueryClientProvider client={clients.at(-1)!}>
        <Probe running={false} />
      </QueryClientProvider>
    )
    await vi.advanceTimersByTimeAsync(0)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(2))
    await vi.advanceTimersByTimeAsync(1000)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(3))
    await vi.advanceTimersByTimeAsync(2000)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(4))
  })

  it("refetches a fresh cached diff when enabled after the run finished", async () => {
    vi.useFakeTimers()
    const getDiff = vi
      .spyOn(agentsApi, "getThreadWorkingTreeDiff")
      .mockResolvedValue(diff)
    const view = renderProbe(true)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(1))

    view.rerender(
      <QueryClientProvider client={clients.at(-1)!}>
        <Probe running enabled={false} />
      </QueryClientProvider>
    )
    view.rerender(
      <QueryClientProvider client={clients.at(-1)!}>
        <Probe running={false} enabled={false} />
      </QueryClientProvider>
    )
    await vi.advanceTimersByTimeAsync(3000)
    expect(getDiff).toHaveBeenCalledTimes(1)

    view.rerender(
      <QueryClientProvider client={clients.at(-1)!}>
        <Probe running={false} enabled />
      </QueryClientProvider>
    )
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(2))
    await vi.advanceTimersByTimeAsync(3000)
    expect(getDiff).toHaveBeenCalledTimes(2)
  })

  it("refetches a fresh cached diff when remounted after the run finished", async () => {
    const getDiff = vi
      .spyOn(agentsApi, "getThreadWorkingTreeDiff")
      .mockResolvedValue(diff)
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    client.setQueryData(agentThreadKeys.workingTreeDiff("thread-1"), diff)
    clients.push(client)

    render(
      <QueryClientProvider client={client}>
        <Probe running={false} />
      </QueryClientProvider>
    )

    await waitFor(() => expect(getDiff).toHaveBeenCalledTimes(1))
  })

  it("cleans delayed final refreshes on unmount", async () => {
    vi.useFakeTimers()
    const getDiff = vi
      .spyOn(agentsApi, "getThreadWorkingTreeDiff")
      .mockResolvedValue(diff)
    const view = renderProbe(true)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(1))

    view.rerender(
      <QueryClientProvider client={clients.at(-1)!}>
        <Probe running={false} />
      </QueryClientProvider>
    )
    await vi.advanceTimersByTimeAsync(0)
    await vi.waitFor(() => expect(getDiff).toHaveBeenCalledTimes(2))
    view.unmount()
    await vi.advanceTimersByTimeAsync(3000)
    expect(getDiff).toHaveBeenCalledTimes(2)
  })
})

describe("normalized thread queries", () => {
  const thread = {
    id: "thread-1",
    title: "Registry thread",
    environment: "cloud",
    status: "idle",
    resolved: false,
  } as AgentThread

  it("stores list membership as ids and joins through the entity cache", async () => {
    vi.spyOn(agentsApi, "listThreadsPage").mockResolvedValue({
      ids: [thread.id],
      items: [thread],
      limit: 100,
      cursor: null,
      hasMore: false,
    })
    const client = testClient()
    const results: Array<ReturnType<typeof useThreadsPage>> = []

    function Probe() {
      results.push(useThreadsPage(params))
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    await waitFor(() => expect(results.at(-1)?.data?.items).toEqual([thread]))
    expect(client.getQueryData(agentThreadKeys.page(params))).toEqual({
      ids: [thread.id],
      limit: 100,
      cursor: null,
      hasMore: false,
    })
    expect(client.getQueryData(agentThreadKeys.detail(thread.id))).toEqual(
      thread
    )
  })

  it("reflects one entity update without rewriting list membership", async () => {
    vi.spyOn(agentsApi, "listThreadsPage").mockResolvedValue({
      ids: [thread.id],
      items: [thread],
      limit: 100,
      cursor: null,
      hasMore: false,
    })
    const client = testClient()
    const results: Array<ReturnType<typeof useThreadsPage>> = []

    function Probe() {
      results.push(useThreadsPage(params))
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )
    await waitFor(() => expect(results.at(-1)?.data?.items).toEqual([thread]))
    const membership = client.getQueryData(agentThreadKeys.page(params))

    act(() => {
      client.setQueryData(agentThreadKeys.detail(thread.id), {
        ...thread,
        status: "running",
      })
    })

    await waitFor(() =>
      expect(results.at(-1)?.data?.items[0]?.status).toBe("running")
    )
    expect(client.getQueryData(agentThreadKeys.page(params))).toBe(membership)
  })

  it("uses the server cursor for the next infinite page", async () => {
    const second = { ...thread, id: "thread-2" }
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockResolvedValueOnce({
        ids: [thread.id],
        items: [thread],
        limit: SIDEBAR_PAGE_SIZE,
        cursor: "next-page",
        hasMore: true,
      })
      .mockResolvedValueOnce({
        ids: [second.id],
        items: [second],
        limit: SIDEBAR_PAGE_SIZE,
        cursor: null,
        hasMore: false,
      })
    const client = testClient()
    let fetchNextPage: (() => Promise<unknown>) | undefined
    let itemCount = 0

    function Probe() {
      const query = useSidebarThreads({})
      fetchNextPage = query.activeQuery.fetchNextPage
      itemCount = query.data.active.items.length
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )
    await waitFor(() => expect(itemCount).toBe(1))
    await act(async () => {
      await fetchNextPage?.()
    })

    await waitFor(() => expect(itemCount).toBe(2))
    expect(listThreads).toHaveBeenLastCalledWith({
      limit: SIDEBAR_PAGE_SIZE,
      resolved: false,
      scope: "interactive",
      sortBy: "created_at",
      cursor: "next-page",
    })
  })

  it("writes a resolved mutation response only to the entity cache", async () => {
    const resolved = { ...thread, resolved: true }
    vi.spyOn(agentsApi, "patchThread").mockResolvedValue(resolved)
    const client = testClient()
    client.setQueryData(agentThreadKeys.detail(thread.id), thread)
    const membership = {
      ids: [thread.id],
      limit: 25,
      cursor: null,
      hasMore: false,
    }
    const key = agentThreadKeys.page({ resolved: false })
    client.setQueryData(key, membership)
    let mutateAsync:
      | ((vars: { threadId: string; resolved: boolean }) => Promise<AgentThread>)
      | undefined

    function Probe() {
      mutateAsync = useResolveAgentThread().mutateAsync
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )
    await act(async () => {
      await mutateAsync?.({ threadId: thread.id, resolved: true })
    })

    expect(client.getQueryData(agentThreadKeys.detail(thread.id))).toEqual(
      resolved
    )
    expect(client.getQueryData(key)).toBe(membership)
  })
})
