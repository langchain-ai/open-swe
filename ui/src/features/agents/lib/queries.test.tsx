/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import { agentThreadKeys, useThreadsPage } from "./queries"
import type { ThreadsPage, ThreadsPageParams } from "./api"

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
