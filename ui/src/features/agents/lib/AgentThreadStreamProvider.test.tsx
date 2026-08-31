/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  AgentThreadStreamProvider,
  __testing,
} from "./AgentThreadStreamProvider"
import type { ReactNode } from "react"

interface ControllerOptions {
  threadId: string | null
  onThreadId: (threadId: string) => void
}

class TestStore<T> {
  private listeners = new Set<() => void>()

  constructor(private snapshot: T) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = () => this.snapshot

  setSnapshot(snapshot: T) {
    this.snapshot = snapshot
    for (const listener of this.listeners) listener()
  }
}

const mocks = vi.hoisted(() => ({
  clients: [] as Array<{ apiUrl: string }>,
  controllers: [] as Array<{
    options: ControllerOptions
    deactivate: ReturnType<typeof vi.fn>
  }>,
  streamController: Symbol("stream-controller"),
}))

vi.mock("@langchain/langgraph-sdk", () => ({
  Client: class Client {
    constructor(options: { apiUrl: string }) {
      mocks.clients.push(options)
    }
  },
  filterOutHeadlessToolInterrupts: (interrupts: Array<unknown>) => interrupts,
  overrideFetchImplementation: vi.fn(),
}))

vi.mock("@langchain/langgraph-sdk/stream", () => ({
  StreamController: class StreamController {
    rootStore: TestStore<{
      values: Record<string, unknown>
      messages: Array<unknown>
      toolCalls: Array<unknown>
      interrupts: Array<unknown>
      isLoading: boolean
      isThreadLoading: boolean
      error: undefined
      threadId: string | null
    }>
    subagentStore = new TestStore({})
    subgraphStore = new TestStore({})
    subgraphByNodeStore = new TestStore({})
    hydrationPromise = Promise.resolve()
    submit = vi.fn()
    stop = vi.fn()
    disconnect = vi.fn()
    respond = vi.fn()
    respondAll = vi.fn()
    getThread = vi.fn()

    constructor(public options: ControllerOptions) {
      this.rootStore = new TestStore({
        values: {},
        messages: [],
        toolCalls: [],
        interrupts: [],
        isLoading: false,
        isThreadLoading: false,
        error: undefined,
        threadId: options.threadId,
      })
    }

    activate() {
      const deactivate = vi.fn()
      mocks.controllers.push({ options: this.options, deactivate })
      return deactivate
    }
  },
}))

vi.mock("@langchain/react", () => ({
  STREAM_CONTROLLER: mocks.streamController,
}))

function wrapper(children: ReactNode) {
  const client = new QueryClient()
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.clients.length = 0
  mocks.controllers.length = 0
})

afterEach(() => {
  cleanup()
  __testing.disposeAll()
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe("AgentThreadStreamProvider", () => {
  it("reuses a controller when navigation returns to a retained thread", () => {
    const view = render(
      wrapper(
        <AgentThreadStreamProvider threadId="one">
          <div />
        </AgentThreadStreamProvider>
      )
    )

    view.rerender(
      wrapper(
        <AgentThreadStreamProvider threadId="two">
          <div />
        </AgentThreadStreamProvider>
      )
    )
    view.rerender(
      wrapper(
        <AgentThreadStreamProvider threadId="one">
          <div />
        </AgentThreadStreamProvider>
      )
    )

    expect(mocks.controllers).toHaveLength(2)
    expect(__testing.entries.size).toBe(2)
  })

  it("isolates cloud and local runtimes with the same thread id", () => {
    const view = render(
      wrapper(
        <AgentThreadStreamProvider threadId="same">
          <div />
        </AgentThreadStreamProvider>
      )
    )
    view.rerender(
      wrapper(
        <AgentThreadStreamProvider threadId="same" transport="local">
          <div />
        </AgentThreadStreamProvider>
      )
    )

    expect(mocks.controllers).toHaveLength(2)
    expect([...__testing.entries.keys()]).toEqual(["cloud:same", "local:same"])
    expect(mocks.clients.map((client) => client.apiUrl)).toEqual([
      "http://localhost:3000/dashboard/api",
      "http://localhost:3000/local-graph",
    ])
  })

  it("rekeys a lazy cloud runtime when LangGraph creates the thread", () => {
    const onThreadId = vi.fn()
    render(
      wrapper(
        <AgentThreadStreamProvider threadId={null} onThreadId={onThreadId}>
          <div />
        </AgentThreadStreamProvider>
      )
    )

    mocks.controllers[0]?.options.onThreadId("created")

    expect([...__testing.entries.keys()]).toEqual(["cloud:created"])
    expect(onThreadId).toHaveBeenCalledWith("created")
  })

  it("disposes an inactive runtime after its retention window", () => {
    const view = render(
      wrapper(
        <AgentThreadStreamProvider threadId="idle">
          <div />
        </AgentThreadStreamProvider>
      )
    )
    view.unmount()

    vi.advanceTimersByTime(60_000)

    expect(__testing.entries.size).toBe(0)
    expect(mocks.controllers[0]?.deactivate).toHaveBeenCalledOnce()
  })

  it("cancels idle disposal when an inactive thread starts running", () => {
    const view = render(
      wrapper(
        <AgentThreadStreamProvider threadId="background-run">
          <div />
        </AgentThreadStreamProvider>
      )
    )
    view.unmount()
    const entry = __testing.entries.get("cloud:background-run")
    if (!entry) throw new Error("runtime was not retained")
    const store = entry.controller.rootStore as unknown as TestStore<
      ReturnType<typeof entry.controller.rootStore.getSnapshot>
    >
    store.setSnapshot({ ...store.getSnapshot(), isLoading: true })

    vi.advanceTimersByTime(60_000)

    expect(__testing.entries.has("cloud:background-run")).toBe(true)
    expect(mocks.controllers[0]?.deactivate).not.toHaveBeenCalled()

    store.setSnapshot({ ...store.getSnapshot(), isLoading: false })
    vi.advanceTimersByTime(60_000)
    expect(__testing.entries.size).toBe(0)
  })

  it("keeps at most eight inactive runtimes", () => {
    for (let index = 0; index < 9; index += 1) {
      const view = render(
        wrapper(
          <AgentThreadStreamProvider threadId={`thread-${index}`}>
            <div />
          </AgentThreadStreamProvider>
        )
      )
      view.unmount()
    }

    expect(__testing.entries.size).toBe(8)
    expect(mocks.controllers[0]?.deactivate).toHaveBeenCalledOnce()
  })
})
