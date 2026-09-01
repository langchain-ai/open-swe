/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
  markAgentThreadViewed,
  setAgentThreadStatus,
  useAgentThreadWorkingTreeDiff,
  useResolveAgentThread,
  useSidebarActiveThread,
  useSidebarProjectThreads,
  useSidebarRecents,
  useThreadsPage,
} from "./queries"
import type { InfiniteData } from "@tanstack/react-query"
import type { ThreadTurnDiff, ThreadsPage, ThreadsPageParams } from "./api"
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

describe("setAgentThreadStatus", () => {
  it("updates paginated sidebar caches", () => {
    const client = testClient()
    const thread = {
      id: "thread-1",
      status: "finished",
      resolved: false,
    } as AgentThread
    const key = agentThreadKeys.infinitePages({
      limit: SIDEBAR_PAGE_SIZE,
      resolved: false,
      scope: "interactive",
      sortBy: "updated_at",
    })
    client.setQueryData(key, {
      pages: [
        {
          items: [thread],
          limit: SIDEBAR_PAGE_SIZE,
          offset: 0,
          hasMore: false,
        },
      ],
      pageParams: [0],
    })
    client.setQueryData(agentThreadKeys.sidebarActive(thread.id), thread)

    setAgentThreadStatus(client, thread.id, "running")

    expect(
      client.getQueryData<InfiniteData<ThreadsPage>>(key)?.pages[0]?.items[0]
    ).toMatchObject({ status: "running" })
    expect(
      client.getQueryData(agentThreadKeys.sidebarActive(thread.id))
    ).toMatchObject({ status: "running" })
  })
})

describe("sidebar queries", () => {
  it("scopes Recents to ownerless threads only in project mode", async () => {
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockResolvedValue(page)
    const client = testClient()
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { rerender } = renderHook(
      ({ projectMode }) => useSidebarRecents({ projectMode }),
      { wrapper, initialProps: { projectMode: true } }
    )

    await waitFor(() =>
      expect(listThreads).toHaveBeenCalledWith({
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
        resolved: false,
        scope: "interactive",
        ownerless: true,
      })
    )

    rerender({ projectMode: false })

    await waitFor(() =>
      expect(listThreads).toHaveBeenCalledWith({
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
        resolved: false,
        scope: "interactive",
      })
    )
  })

  it("paginates each project through an independent query", async () => {
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockImplementation(async (request) => {
        const requestParams = request ?? {}
        const offset = requestParams.offset ?? 0
        return {
          items: [
            {
              id: `${requestParams.repo}-${offset}`,
              repoFullName: requestParams.repo,
            } as AgentThread,
          ],
          limit: requestParams.limit ?? SIDEBAR_PAGE_SIZE,
          offset,
          hasMore: offset === 0,
        }
      })
    const client = testClient()
    const { result } = renderHook(
      () => ({
        first: useSidebarProjectThreads({
          repoFullName: "langchain-ai/first",
        }),
        second: useSidebarProjectThreads({
          repoFullName: "langchain-ai/second",
        }),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    )

    await waitFor(() => {
      expect(result.current.first.items[0]?.id).toBe("langchain-ai/first-0")
      expect(result.current.second.items[0]?.id).toBe("langchain-ai/second-0")
    })

    act(() => result.current.first.fetchNextPage())

    await waitFor(() =>
      expect(listThreads).toHaveBeenCalledWith(
        expect.objectContaining({
          repo: "langchain-ai/first",
          offset: 1,
        })
      )
    )
    expect(listThreads).not.toHaveBeenCalledWith(
      expect.objectContaining({
        repo: "langchain-ai/second",
        offset: 1,
      })
    )
  })

  it("fetches the active thread when it is outside the loaded pages", async () => {
    const opened = { id: "opened-thread", resolved: false } as AgentThread
    const getThread = vi.spyOn(agentsApi, "getThread").mockResolvedValue(opened)
    const client = testClient()
    const { result } = renderHook(
      () =>
        useSidebarActiveThread({
          activeThreadId: opened.id,
          loadedThreads: [],
        }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    )

    await waitFor(() => expect(result.current).toEqual(opened))
    expect(getThread).toHaveBeenCalledWith(opened.id, { markViewed: false })
  })
})

describe("useResolveAgentThread", () => {
  it("rolls back an optimistic resolution when the request fails", async () => {
    const opened = {
      id: "opened-thread",
      status: "idle",
      resolved: false,
    } as AgentThread
    let failResolve: ((error: Error) => void) | undefined
    vi.spyOn(agentsApi, "resolveThread").mockReturnValue(
      new Promise<AgentThread>((_resolve, reject) => {
        failResolve = reject
      })
    )
    const client = testClient()
    const key = agentThreadKeys.page({ resolved: false })
    const unrelated = { ...opened, id: "unrelated-thread", title: "Before" }
    client.setQueryData(agentThreadKeys.detail(opened.id), opened)
    client.setQueryData(agentThreadKeys.detail(unrelated.id), unrelated)
    client.setQueryData(key, {
      items: [opened],
      limit: 25,
      offset: 0,
      hasMore: false,
    })
    const { result } = renderHook(() => useResolveAgentThread(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    })

    act(() => {
      result.current.mutate({ threadId: opened.id, resolved: true })
    })
    await waitFor(() =>
      expect(client.getQueryData<ThreadsPage>(key)?.items).toEqual([])
    )
    expect(
      client.getQueryData<AgentThread>(agentThreadKeys.detail(opened.id))
    ).toMatchObject({ resolved: true })
    client.setQueryData(agentThreadKeys.detail(unrelated.id), {
      ...unrelated,
      title: "After",
    })

    act(() => failResolve?.(new Error("request failed")))
    await waitFor(() =>
      expect(client.getQueryData<ThreadsPage>(key)?.items).toEqual([opened])
    )
    expect(
      client.getQueryData<AgentThread>(agentThreadKeys.detail(opened.id))
    ).toEqual(opened)
    expect(
      client.getQueryData(agentThreadKeys.sidebarActive(opened.id))
    ).toBeUndefined()
    expect(
      client.getQueryData<AgentThread>(agentThreadKeys.detail(unrelated.id))
    ).toMatchObject({ title: "After" })
  })
})

describe("markAgentThreadViewed", () => {
  const unread = { id: "thread-1", viewed: false } as AgentThread

  it("clears the unread flag across the caches, leaving the detail stale", () => {
    const client = testClient()
    const pagesKey = agentThreadKeys.infinitePages({ limit: 10 })
    client.setQueryData<Array<AgentThread>>(agentThreadKeys.pinned, [unread])
    client.setQueryData<InfiniteData<ThreadsPage>>(pagesKey, {
      pages: [{ items: [unread], limit: 10, offset: 0, hasMore: false }],
      pageParams: [0],
    })
    client.setQueryData(agentThreadKeys.sidebarActive("thread-1"), unread)
    client.setQueryData(agentThreadKeys.detail("thread-1"), unread)

    markAgentThreadViewed(client, "thread-1")

    expect(
      client.getQueryData<Array<AgentThread>>(agentThreadKeys.pinned)?.[0]
        ?.viewed
    ).toBe(true)
    expect(
      client.getQueryData<InfiniteData<ThreadsPage>>(pagesKey)?.pages[0]
        ?.items[0]?.viewed
    ).toBe(true)
    expect(
      client.getQueryData<AgentThread>(
        agentThreadKeys.sidebarActive("thread-1")
      )?.viewed
    ).toBe(true)
    // A fresh detail entry would suppress the refetch that marks the thread
    // viewed server-side, and the dot would come back on the next list refetch.
    expect(
      client.getQueryData<AgentThread>(agentThreadKeys.detail("thread-1"))
        ?.viewed
    ).toBe(true)
    expect(
      client.getQueryState(agentThreadKeys.detail("thread-1"))?.dataUpdatedAt
    ).toBe(0)
  })
})
