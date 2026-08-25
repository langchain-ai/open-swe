/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ThreadEventsProvider } from "./ThreadEventsProvider"
import { agentThreadKeys } from "./queries"
import type { AgentThread } from "./types"
import type { InfiniteData } from "@tanstack/react-query"
import type { ReactNode } from "react"

const streamState = vi.hoisted(() => ({ isLoading: false }))

vi.mock("@langchain/react", () => ({
  useStreamContext: () => streamState,
}))

interface IdPage {
  ids: Array<string>
  limit: number
  cursor: string | null
  hasMore: boolean
}

class FakeEventSource {
  static current: FakeEventSource | null = null
  listeners = new Map<string, (event: MessageEvent<string>) => void>()

  constructor(_url: URL, _options: EventSourceInit) {
    FakeEventSource.current = this
  }

  addEventListener(kind: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(kind, listener as (event: MessageEvent<string>) => void)
  }

  emit(kind: string, threadId: string, payload: Record<string, unknown>) {
    this.listeners.get(kind)?.({
      data: JSON.stringify({ id: 1, thread_id: threadId, kind, payload }),
    } as MessageEvent<string>)
  }

  close() {
    if (FakeEventSource.current === this) FakeEventSource.current = null
  }
}

const thread = (overrides: Partial<AgentThread> = {}): AgentThread => ({
  id: "thread-1",
  title: "Thread",
  repo: "repo",
  repoFullName: "org/repo",
  branch: "main",
  model: "Default",
  effort: null,
  source: "dashboard",
  environment: "cloud",
  status: "idle",
  viewed: true,
  isOwner: true,
  createdAt: 1,
  updatedAt: 1,
  traceUrl: null,
  sandboxId: null,
  messages: [],
  ...overrides,
})

function provider(client: QueryClient, activeThreadId?: string) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ThreadEventsProvider activeThreadId={activeThreadId}>
        {children}
      </ThreadEventsProvider>
    </QueryClientProvider>
  )
  return Wrapper
}

async function source() {
  await waitFor(() => expect(FakeEventSource.current).not.toBeNull())
  return FakeEventSource.current as FakeEventSource
}

describe("ThreadEventsProvider", () => {
  beforeEach(() => {
    FakeEventSource.current = null
    streamState.isLoading = false
    vi.stubGlobal("EventSource", FakeEventSource)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("patches one entity shared by every list without invalidating lists", async () => {
    const client = new QueryClient()
    const existing = thread()
    const pageKey = agentThreadKeys.page({ limit: 10 })
    const infiniteKey = agentThreadKeys.infinitePages({ limit: 10 })
    client.setQueryData(agentThreadKeys.detail(existing.id), existing)
    client.setQueryData<IdPage>(pageKey, {
      ids: [existing.id],
      limit: 10,
      cursor: null,
      hasMore: false,
    })
    client.setQueryData<InfiniteData<IdPage>>(infiniteKey, {
      pages: [{ ids: [existing.id], limit: 10, cursor: null, hasMore: false }],
      pageParams: [null],
    })
    const invalidate = vi.spyOn(client, "invalidateQueries")
    render(<div />, { wrapper: provider(client) })
    const events = await source()

    act(() => events.emit("thread.status", existing.id, thread({ status: "running" })))
    await waitFor(() =>
      expect(client.getQueryData<AgentThread>(agentThreadKeys.detail(existing.id))?.status).toBe(
        "running"
      )
    )

    expect(client.getQueryData<IdPage>(pageKey)?.ids).toEqual([existing.id])
    expect(client.getQueryData<InfiniteData<IdPage>>(infiniteKey)?.pages[0]?.ids).toEqual([
      existing.id,
    ])
    expect(invalidate).not.toHaveBeenCalled()
  })

  it("moves created and handed-off threads between filtered ID lists", async () => {
    const client = new QueryClient()
    const localKey = agentThreadKeys.page({ limit: 10, environment: "local" })
    const cloudKey = agentThreadKeys.page({ limit: 10, environment: "cloud" })
    for (const key of [localKey, cloudKey]) {
      client.setQueryData<IdPage>(key, {
        ids: [],
        limit: 10,
        cursor: null,
        hasMore: false,
      })
    }
    render(<div />, { wrapper: provider(client) })
    const events = await source()

    act(() => events.emit("thread.created", "thread-1", thread({ environment: "local" })))
    expect(client.getQueryData<IdPage>(localKey)?.ids).toEqual(["thread-1"])
    expect(client.getQueryData<IdPage>(cloudKey)?.ids).toEqual([])

    act(() => events.emit("thread.handoff", "thread-1", thread({ environment: "cloud" })))
    expect(client.getQueryData<IdPage>(localKey)?.ids).toEqual([])
    expect(client.getQueryData<IdPage>(cloudKey)?.ids).toEqual(["thread-1"])
  })

  it("removes deleted IDs even when the entity is not cached", async () => {
    const client = new QueryClient()
    const key = agentThreadKeys.page({ limit: 10 })
    client.setQueryData<IdPage>(key, {
      ids: ["thread-1"],
      limit: 10,
      cursor: null,
      hasMore: false,
    })
    render(<div />, { wrapper: provider(client) })
    const events = await source()

    act(() => events.emit("thread.deleted", "thread-1", { id: "thread-1" }))

    expect(client.getQueryData<IdPage>(key)?.ids).toEqual([])
  })

  it("invalidates messages only for the open non-streaming thread", async () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, "invalidateQueries")
    const Wrapper = provider(client, "thread-1")
    const view = render(<div />, { wrapper: Wrapper })
    const events = await source()

    act(() => events.emit("thread.message", "thread-2", { thread_id: "thread-2", seq: 1 }))
    expect(invalidate).not.toHaveBeenCalled()
    act(() => events.emit("thread.message", "thread-1", { thread_id: "thread-1", seq: 1 }))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: agentThreadKeys.messages("thread-1") })

    invalidate.mockClear()
    streamState.isLoading = true
    view.rerender(<div />)
    act(() => events.emit("thread.message", "thread-1", { thread_id: "thread-1", seq: 2 }))
    expect(invalidate).not.toHaveBeenCalled()
  })
})
