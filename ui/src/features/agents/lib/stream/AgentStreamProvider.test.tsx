/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentStreamProvider, useAgentStream } from "./AgentStreamProvider"
import { useStreamConnection } from "./streamConnection"
import type { ReactNode } from "react"

interface StreamOptions {
  threadId: string | null
  maxReconnectAttempts: number
  reconnectDelayMs: (attempt: number) => number
  onReconnect: (options: { attempt: number; delayMs: number }) => void
  onConnected: () => void
  onThreadId: (threadId: string) => void
  onCreated: () => void
  onCompleted: (info: { reason: "success" }) => void
}

interface StreamHandle {
  threadId: string | null
  isLoading: boolean
  getThread: () => { onError: (listener: (error: Error) => void) => () => void }
}

const mocks = vi.hoisted(() => ({
  streams: [] as Array<StreamOptions>,
  threadErrors: [] as Array<(error: Error) => void>,
  onEvent: (_event: unknown) => {},
}))

vi.mock("@langchain/react", () => ({
  useChannelEffect: (
    _stream: unknown,
    channels: Array<string>,
    options: { onEvent: (event: unknown) => void }
  ) => {
    // The provider also subscribes lifecycle/messages for perf tracking.
    if (channels.includes("custom")) mocks.onEvent = options.onEvent
  },
  useStream: (options: StreamOptions) => {
    mocks.streams.push(options)
    return {
      threadId: options.threadId,
      isLoading: false,
      getThread: () => ({
        onError: (listener: (error: Error) => void) => {
          mocks.threadErrors.push(listener)
          return () => {}
        },
      }),
    } satisfies StreamHandle
  },
}))

vi.mock("@/lib/langgraph-client", () => ({
  absoluteApiUrl: (url: string) => url,
  createDashboardClient: () => ({}),
  createLocalGraphClient: () => ({}),
  dashboardFetch: fetch,
}))

vi.mock("./lazyHydration", () => ({
  withLazyHydration: (client: unknown) => client,
}))

function Probe() {
  const stream = useAgentStream()
  return <output>{stream.threadId ?? "new"}</output>
}

function OffloadingProbe() {
  return (
    <output>{useAgentStream().isOffloading ? "offloading" : "idle"}</output>
  )
}

function wrapper(children: ReactNode) {
  const client = new QueryClient()
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function latest(): StreamOptions {
  const options = mocks.streams.at(-1)
  if (!options) throw new Error("no stream mounted")
  return options
}

beforeEach(() => {
  mocks.streams.length = 0
  mocks.threadErrors.length = 0
  useStreamConnection.setState({ connection: { status: "live" } })
})

afterEach(() => {
  cleanup()
})

describe("AgentStreamProvider", () => {
  it("tracks only root offloading events and clears on completion", () => {
    const view = render(
      wrapper(
        <AgentStreamProvider threadId="t1">
          <OffloadingProbe />
        </AgentStreamProvider>
      )
    )
    const emit = (status: string, namespace: Array<string> = []) =>
      act(() =>
        mocks.onEvent({
          method: "custom",
          params: {
            namespace,
            data: { payload: { type: "conversation_offloading", status } },
          },
        })
      )

    emit("started", ["subagent:one"])
    expect(view.getByRole("status").textContent).toBe("idle")
    for (const status of ["completed", "failed"]) {
      emit("started")
      expect(view.getByRole("status").textContent).toBe("offloading")
      emit(status)
      expect(view.getByRole("status").textContent).toBe("idle")
    }
    emit("started")
    act(() => latest().onCompleted({ reason: "success" }))
    expect(view.getByRole("status").textContent).toBe("idle")
  })

  it("tracks reconnect attempts until the stream reconnects", () => {
    render(
      wrapper(
        <AgentStreamProvider threadId="t1">
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onReconnect({ attempt: 2, delayMs: 2_000 }))
    expect(useStreamConnection.getState().connection).toMatchObject({
      status: "reconnecting",
      attempt: 2,
    })
    act(() => latest().onConnected())
    expect(useStreamConnection.getState().connection.status).toBe("live")
  })

  it("clears reconnect state when the stream gives up", () => {
    render(
      wrapper(
        <AgentStreamProvider threadId="t1">
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onReconnect({ attempt: 12, delayMs: 300_000 }))
    act(() => {
      for (const listener of mocks.threadErrors) listener(new Error("gone"))
    })
    expect(useStreamConnection.getState().connection.status).toBe("live")
  })

  it("follows the route's thread on the same stream", () => {
    const view = render(
      wrapper(
        <AgentStreamProvider threadId="t1">
          <Probe />
        </AgentStreamProvider>
      )
    )
    expect(view.getByRole("status").textContent).toBe("t1")
    act(() => latest().onReconnect({ attempt: 1, delayMs: 1_000 }))
    view.rerender(
      wrapper(
        <AgentStreamProvider threadId="t2">
          <Probe />
        </AgentStreamProvider>
      )
    )
    expect(view.getByRole("status").textContent).toBe("t2")
    expect(latest().threadId).toBe("t2")
    // The previous thread's retry countdown does not follow the user over.
    expect(useStreamConnection.getState().connection.status).toBe("live")
  })

  it("announces a lazy cloud thread only once the server accepts its run", () => {
    const created: Array<string> = []
    render(
      wrapper(
        <AgentStreamProvider
          threadId={null}
          onThreadCreated={(id) => created.push(id)}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onThreadId("minted"))
    expect(created).toEqual([])
    act(() => latest().onCreated())
    expect(created).toEqual(["minted"])
  })

  it("does not treat a follow-up run on an existing thread as a creation", () => {
    const created: Array<string> = []
    render(
      wrapper(
        <AgentStreamProvider
          threadId="t1"
          onThreadCreated={(id) => created.push(id)}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onThreadId("t1"))
    act(() => latest().onCreated())
    expect(created).toEqual([])
  })

  it("does not announce a lazy thread the user has already left", () => {
    const created: Array<string> = []
    const view = render(
      wrapper(
        <AgentStreamProvider
          threadId={null}
          onThreadCreated={(id) => created.push(id)}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onThreadId("minted"))
    view.rerender(
      wrapper(
        <AgentStreamProvider
          threadId="other"
          onThreadCreated={(id) => created.push(id)}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onCreated())
    expect(created).toEqual([])
  })

  it("never announces local threads", () => {
    const created: Array<string> = []
    render(
      wrapper(
        <AgentStreamProvider
          threadId={null}
          transport="local"
          onThreadCreated={(id) => created.push(id)}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => latest().onThreadId("minted"))
    act(() => latest().onCreated())
    expect(created).toEqual([])
  })
})
