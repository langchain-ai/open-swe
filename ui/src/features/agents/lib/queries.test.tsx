/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  SIDEBAR_PAGE_SIZE,
  agentThreadKeys,
  useSidebarThreads,
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

function testClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return client
}

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
    })
  })

  it("keeps an opened resolved thread visible when resolved threads are hidden", async () => {
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

    await waitFor(() => expect(sidebar?.data.active.items).toEqual([opened]))
    expect(getThread).toHaveBeenCalledWith(opened.id, { markViewed: false })
  })
})
