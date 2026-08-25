/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
  setAgentThreadStatus,
  useAgentThreadWorkingTreeDiff,
  useResolveAgentThread,
  useSidebarThreads,
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

describe("useSidebarThreads", () => {
  it("loads ten active threads and defers resolved threads", async () => {
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockImplementation((request) =>
        Promise.resolve({
          items: [],
          limit: request?.limit ?? 25,
          offset: request?.offset ?? 0,
          hasMore: false,
        })
      )
    const client = testClient()

    function Probe({ includeResolved }: { includeResolved: boolean }) {
      useSidebarThreads({ includeResolved })
      return null
    }

    const view = render(
      <QueryClientProvider client={client}>
        <Probe includeResolved={false} />
      </QueryClientProvider>
    )

    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(1))
    expect(listThreads).toHaveBeenCalledWith({
      limit: SIDEBAR_PAGE_SIZE,
      offset: 0,
      resolved: false,
      scope: "interactive",
      sortBy: "updated_at",
    })

    view.rerender(
      <QueryClientProvider client={client}>
        <Probe includeResolved />
      </QueryClientProvider>
    )

    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(2))
    expect(listThreads).toHaveBeenLastCalledWith({
      limit: SIDEBAR_PAGE_SIZE,
      offset: 0,
      resolved: true,
      scope: "interactive",
      sortBy: "updated_at",
    })
  })

  it("appends the next active page", async () => {
    const activeThreads = Array.from(
      { length: SIDEBAR_PAGE_SIZE * 2 },
      (_, index) =>
        ({
          id: `thread-${index}`,
          status: "idle",
          resolved: false,
        }) as AgentThread
    )
    vi.spyOn(agentsApi, "listThreadsPage").mockImplementation((request) => {
      const offset = request?.offset ?? 0
      return Promise.resolve({
        items: activeThreads.slice(offset, offset + SIDEBAR_PAGE_SIZE),
        limit: SIDEBAR_PAGE_SIZE,
        offset,
        hasMore: offset === 0,
      })
    })
    const client = testClient()
    let sidebar: ReturnType<typeof useSidebarThreads> | undefined

    function Probe() {
      sidebar = useSidebarThreads({})
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    await waitFor(() =>
      expect(sidebar?.data.active.items).toHaveLength(SIDEBAR_PAGE_SIZE)
    )
    await act(async () => {
      await sidebar?.activeQuery.fetchNextPage()
    })

    await waitFor(() =>
      expect(sidebar?.data.active.items).toHaveLength(SIDEBAR_PAGE_SIZE * 2)
    )
    expect(agentsApi.listThreadsPage).toHaveBeenLastCalledWith({
      limit: SIDEBAR_PAGE_SIZE,
      offset: SIDEBAR_PAGE_SIZE,
      resolved: false,
      scope: "interactive",
      sortBy: "updated_at",
    })
  })

  it("hides an opened resolved thread when resolved threads are hidden", async () => {
    vi.spyOn(agentsApi, "listThreadsPage").mockResolvedValue({
      items: [],
      limit: SIDEBAR_PAGE_SIZE,
      offset: 0,
      hasMore: false,
    })
    const opened = {
      id: "opened-thread",
      status: "idle",
      resolved: true,
    } as AgentThread
    const getThread = vi.spyOn(agentsApi, "getThread").mockResolvedValue(opened)
    const client = testClient()
    let sidebar: ReturnType<typeof useSidebarThreads> | undefined

    function Probe() {
      sidebar = useSidebarThreads({ activeThreadId: opened.id })
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    await waitFor(() => expect(getThread).toHaveBeenCalledTimes(1))
    expect(sidebar?.data.active.items).toEqual([])
    expect(getThread).toHaveBeenCalledWith(opened.id, { markViewed: false })
  })

  it("hides the opened thread while resolving it optimistically", async () => {
    const opened = {
      id: "opened-thread",
      status: "idle",
      resolved: false,
    } as AgentThread
    const resolved = { ...opened, resolved: true }
    const listThreads = vi
      .spyOn(agentsApi, "listThreadsPage")
      .mockResolvedValueOnce({
        items: [opened],
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
        hasMore: false,
      })
      .mockResolvedValue({
        items: [],
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
        hasMore: false,
      })
    let finishResolve: ((thread: AgentThread) => void) | undefined
    const resolveRequest = new Promise<AgentThread>((resolve) => {
      finishResolve = resolve
    })
    vi.spyOn(agentsApi, "resolveThread").mockReturnValue(resolveRequest)
    const getThread = vi.spyOn(agentsApi, "getThread")
    const client = testClient()
    let sidebar: ReturnType<typeof useSidebarThreads> | undefined
    let resolveThread: ReturnType<typeof useResolveAgentThread> | undefined

    function Probe() {
      sidebar = useSidebarThreads({ activeThreadId: opened.id })
      resolveThread = useResolveAgentThread()
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    await waitFor(() => expect(sidebar?.data.active.items).toEqual([opened]))
    act(() => {
      resolveThread?.mutate({ threadId: opened.id, resolved: true })
    })

    await waitFor(() => expect(sidebar?.data.active.items).toEqual([]))
    expect(resolveThread?.isPending).toBe(true)
    expect(getThread).not.toHaveBeenCalled()

    await act(async () => {
      finishResolve?.(resolved)
      await resolveRequest
    })
    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(2))
  })

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
    let resolveThread: ReturnType<typeof useResolveAgentThread> | undefined

    function Probe() {
      resolveThread = useResolveAgentThread()
      return null
    }

    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>
    )

    act(() => {
      resolveThread?.mutate({ threadId: opened.id, resolved: true })
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
