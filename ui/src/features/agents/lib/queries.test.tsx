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
  const sidebarPage = (
    items: Array<AgentThread> = [],
    { offset = 0, hasMore = false }: { offset?: number; hasMore?: boolean } = {}
  ) => ({
    pinned: [],
    recents: { items, limit: SIDEBAR_PAGE_SIZE, offset, hasMore },
  })

  it("asks for archived threads only when they are shown", async () => {
    const listThreads = vi
      .spyOn(agentsApi, "listSidebarThreads")
      .mockResolvedValue(sidebarPage())
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
    expect(listThreads).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeResolved: false, offset: 0 })
    )

    view.rerender(
      <QueryClientProvider client={client}>
        <Probe includeResolved />
      </QueryClientProvider>
    )

    await waitFor(() => expect(listThreads).toHaveBeenCalledTimes(2))
    expect(listThreads).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeResolved: true })
    )
  })

  it("surfaces a selected thread the recents page does not carry", async () => {
    const opened = { id: "opened-thread", resolved: false } as AgentThread
    vi.spyOn(agentsApi, "listSidebarThreads").mockResolvedValue(sidebarPage())
    const getThread = vi.spyOn(agentsApi, "getThread").mockResolvedValue(opened)
    const client = testClient()
    const { result } = renderHook(
      () => useSidebarThreads({ activeThreadId: opened.id }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    )

    await waitFor(() => expect(result.current.activeThread).toEqual(opened))
    expect(getThread).toHaveBeenCalledWith(opened.id, { markViewed: false })
  })

  it("pages recents by offset", async () => {
    const first = { id: "first" } as AgentThread
    const second = { id: "second" } as AgentThread
    const listThreads = vi
      .spyOn(agentsApi, "listSidebarThreads")
      .mockResolvedValueOnce(sidebarPage([first], { hasMore: true }))
      .mockResolvedValueOnce(sidebarPage([second], { offset: 1 }))
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

    await waitFor(() => expect(sidebar?.recents).toEqual([first]))
    act(() => sidebar?.fetchNextPage())
    await waitFor(() => expect(sidebar?.recents).toEqual([first, second]))
    expect(listThreads).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 1 })
    )
  })

  it("marks the opened thread resolved optimistically", async () => {
    const opened = {
      id: "opened-thread",
      status: "idle",
      resolved: false,
    } as AgentThread
    const resolved = { ...opened, resolved: true }
    vi.spyOn(agentsApi, "listSidebarThreads").mockResolvedValue(
      sidebarPage([opened])
    )
    let finishResolve: ((thread: AgentThread) => void) | undefined
    const resolveRequest = new Promise<AgentThread>((resolve) => {
      finishResolve = resolve
    })
    vi.spyOn(agentsApi, "resolveThread").mockReturnValue(resolveRequest)
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

    await waitFor(() => expect(sidebar?.recents).toEqual([opened]))
    act(() => {
      resolveThread?.mutate({ threadId: opened.id, resolved: true })
    })

    await waitFor(() => expect(sidebar?.recents).toEqual([resolved]))

    await act(async () => {
      finishResolve?.(resolved)
      await resolveRequest
    })
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

describe("markAgentThreadViewed", () => {
  const unread = { id: "thread-1", viewed: false } as AgentThread

  it("clears the unread flag across the caches, leaving the detail stale", () => {
    const client = testClient()
    const sidebarKey = [...agentThreadKeys.lists, "sidebar", { limit: 10 }]
    client.setQueryData(sidebarKey, {
      pages: [
        {
          pinned: [unread],
          recents: { items: [unread], limit: 10, offset: 0, hasMore: false },
        },
      ],
      pageParams: [0],
    })
    client.setQueryData(agentThreadKeys.sidebarActive("thread-1"), unread)
    client.setQueryData(agentThreadKeys.detail("thread-1"), unread)

    markAgentThreadViewed(client, "thread-1")

    const sidebar = client.getQueryData<
      InfiniteData<{
        pinned: Array<AgentThread>
        recents: { items: Array<AgentThread> }
      }>
    >(sidebarKey)
    expect(sidebar?.pages[0]?.pinned[0]?.viewed).toBe(true)
    expect(sidebar?.pages[0]?.recents.items[0]?.viewed).toBe(true)
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
