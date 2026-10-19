// @vitest-environment jsdom

import { InfiniteQueryObserver, QueryClient } from "@tanstack/react-query"
import { waitFor } from "@testing-library/react"
import { expect, it, vi } from "vitest"

import { InvalidationTab } from "./client"

it("keeps a loading next page when its invalidation arrives and revalidates afterwards", async () => {
  const sources: Array<EventTarget> = []
  vi.stubGlobal("BroadcastChannel", undefined)
  vi.stubGlobal(
    "EventSource",
    class extends EventTarget {
      constructor() {
        super()
        sources.push(this)
      }
      close() {}
    }
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  let finishPage = () => {}
  const pendingPage = new Promise<void>((resolve) => {
    finishPage = resolve
  })
  const queryFn = vi.fn(async ({ pageParam }: { pageParam: number }) => {
    if (pageParam === 1) await pendingPage
    return { items: [pageParam], next: pageParam + 1 }
  })
  const observer = new InfiniteQueryObserver(client, {
    queryKey: ["sidebar-pages"],
    queryFn,
    initialPageParam: 0,
    getNextPageParam: (page) => page.next,
    meta: { invalidatedBy: ["threads"] },
  })
  const unsubscribe = observer.subscribe(() => {})
  const tab = new InvalidationTab(client, () => {})
  try {
    await waitFor(() =>
      expect(observer.getCurrentResult().data?.pages).toHaveLength(1)
    )
    tab.start()
    await waitFor(() => expect(sources).toHaveLength(1))
    sources[0]!.dispatchEvent(
      new MessageEvent("hello", {
        data: JSON.stringify({
          backend_commit: null,
          invalidated: [],
          denied: [],
        }),
      })
    )
    const loading = observer.fetchNextPage()
    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2))
    sources[0]!.dispatchEvent(
      new MessageEvent("invalidated", {
        data: JSON.stringify({ topics: ["threads"] }),
      })
    )
    finishPage()
    await loading
    await waitFor(() => {
      expect(observer.getCurrentResult().isFetching).toBe(false)
      expect(
        observer.getCurrentResult().data?.pages.map((page) => page.items)
      ).toEqual([[0], [1]])
      expect(queryFn).toHaveBeenCalledTimes(4)
    })
  } finally {
    tab.stop()
    unsubscribe()
    client.clear()
    vi.unstubAllGlobals()
  }
})
