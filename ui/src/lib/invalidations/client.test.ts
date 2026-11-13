import {
  InfiniteQueryObserver,
  QueryClient,
  QueryObserver,
} from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { InvalidationTab } from "./client"

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("coalesces PR events during a slow fetch and preserves the trailing update", async () => {
  vi.useFakeTimers()
  vi.stubGlobal("window", new EventTarget())
  let receive: ((event: { data: unknown }) => void) | null = null
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      set onmessage(handler: (event: { data: unknown }) => void) {
        receive = handler
      }
      postMessage() {}
      close() {}
    }
  )
  vi.stubGlobal("navigator", {
    locks: { request: () => new Promise(() => {}) },
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  let finish: (value: number) => void = () => {}
  const read = vi.fn(
    () =>
      new Promise<number>((resolve) => {
        finish = resolve
      })
  )
  const observer = new QueryObserver(client, {
    queryKey: ["status"],
    initialData: 0,
    staleTime: Infinity,
    meta: { invalidatedBy: ["pull-requests/acme/api/1"] },
    queryFn: read,
  })
  const unsubscribe = observer.subscribe(() => {})
  const tab = new InvalidationTab(client, () => {})
  tab.start()
  const invalidate = () =>
    receive?.({
      data: {
        kind: "invalidated",
        topics: ["pull-requests/acme/api/1"],
      },
    })
  try {
    invalidate()
    expect(read).toHaveBeenCalledTimes(1)
    for (let i = 0; i < 50; i++) invalidate()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(read).toHaveBeenCalledTimes(1)
    finish(1)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(4_999)
    expect(read).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(read).toHaveBeenCalledTimes(2)
    finish(2)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(observer.getCurrentResult().data).toBe(2)
    expect(read).toHaveBeenCalledTimes(2)
    invalidate()
    expect(read).toHaveBeenCalledTimes(3)
    invalidate()
    tab.stop()
    finish(3)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(read).toHaveBeenCalledTimes(3)
  } finally {
    tab.stop()
    unsubscribe()
    client.clear()
  }
})

it("retains a page loaded while a thread invalidation arrives", async () => {
  vi.useFakeTimers()
  vi.stubGlobal("window", new EventTarget())
  let receive: ((event: { data: unknown }) => void) | null = null
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      set onmessage(handler: (event: { data: unknown }) => void) {
        receive = handler
      }
      postMessage() {}
      close() {}
    }
  )
  vi.stubGlobal("navigator", {
    locks: { request: () => new Promise(() => {}) },
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  let finish: (page: { items: number[]; hasMore: boolean }) => void = () => {}
  const read = vi
    .fn(async ({ pageParam }: { pageParam: number }) => ({
      items: [pageParam === 0 ? 10 : pageParam],
      hasMore: pageParam === 0,
    }))
    .mockImplementationOnce(
      () =>
        new Promise<{ items: number[]; hasMore: boolean }>((resolve) => {
          finish = resolve
        })
    )
  const observer = new InfiniteQueryObserver(client, {
    queryKey: ["threads"],
    initialData: { pages: [{ items: [0], hasMore: true }], pageParams: [0] },
    initialPageParam: 0,
    staleTime: Infinity,
    meta: { invalidatedBy: ["threads/alice"] },
    getNextPageParam: (page, pages) =>
      page.hasMore ? pages.length : undefined,
    queryFn: read,
  })
  const unsubscribe = observer.subscribe(() => {})
  const tab = new InvalidationTab(client, () => {})
  tab.start()
  try {
    const next = observer.fetchNextPage()
    const invalidate = () =>
      receive?.({ data: { kind: "invalidated", topics: ["threads/alice"] } })
    invalidate()
    finish({ items: [1], hasMore: false })
    await next
    await vi.advanceTimersByTimeAsync(0)
    expect(
      observer.getCurrentResult().data?.pages.flatMap((page) => page.items)
    ).toEqual([10, 1])
    expect(observer.getCurrentResult().hasNextPage).toBe(false)
  } finally {
    tab.stop()
    unsubscribe()
    client.clear()
  }
})
