/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useAgentThreadStream } from "./useAgentThreadStream"
import type { ReactNode } from "react"

interface StreamOptions {
  threadId: string
  maxReconnectAttempts: number
  reconnectDelayMs: (attempt: number) => number
  onReconnect: (options: { attempt: number; delayMs: number }) => void
  onConnected: () => void
  onCompleted: (info: { reason: "success" }) => void
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
    // The hook also subscribes lifecycle/messages for perf tracking.
    if (channels.includes("custom")) mocks.onEvent = options.onEvent
  },
  useStream: (options: StreamOptions) => {
    mocks.streams.push(options)
    return {
      threadId: options.threadId,
      isLoading: true,
      getThread: () => ({
        onError: (listener: (error: Error) => void) => {
          mocks.threadErrors.push(listener)
          return () => {}
        },
      }),
    }
  },
}))

vi.mock("@/lib/langgraph-client", () => ({
  createDashboardClient: () => ({}),
  createLocalGraphClient: () => ({}),
  dashboardFetch: fetch,
}))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient()
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function render() {
  return renderHook(
    () => useAgentThreadStream({ transport: "cloud", threadId: "one" }),
    { wrapper }
  )
}

function lastStream(): StreamOptions {
  const stream = mocks.streams.at(-1)
  if (!stream) throw new Error("stream was not mounted")
  return stream
}

beforeEach(() => {
  mocks.streams.length = 0
  mocks.threadErrors.length = 0
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("useAgentThreadStream", () => {
  it("tracks only root offloading events and clears on completion", () => {
    const view = render()
    const emit = (status: string, namespace: Array<string> = []) =>
      act(() =>
        mocks.onEvent({
          method: "custom",
          params: {
            namespace,
            data: {
              payload: {
                type: "conversation_offloading",
                status,
                trigger: "manual",
              },
            },
          },
        })
      )

    emit("started", ["subagent:one"])
    expect(view.result.current.stream.isOffloading).toBe(false)
    for (const status of ["completed", "skipped", "failed"]) {
      emit("started")
      expect(view.result.current.stream.isOffloading).toBe(true)
      emit(status)
      expect(view.result.current.stream.isOffloading).toBe(false)
    }

    emit("started")
    act(() => lastStream().onCompleted({ reason: "success" }))
    expect(view.result.current.stream.isOffloading).toBe(false)
  })

  it("waits before surfacing reconnect attempts and clears brief interruptions", () => {
    vi.useFakeTimers()
    const view = render()
    const stream = lastStream()

    expect(stream.maxReconnectAttempts).toBe(12)
    expect(stream.reconnectDelayMs(12)).toBe(300_000)

    act(() => stream.onReconnect({ attempt: 1, delayMs: 1_000 }))
    act(() => vi.advanceTimersByTime(2_000))
    act(() => stream.onConnected())
    act(() => vi.advanceTimersByTime(1_000))
    expect(view.result.current.connection).toEqual({ status: "live" })

    act(() => stream.onReconnect({ attempt: 2, delayMs: 2_000 }))
    act(() => vi.advanceTimersByTime(3_000))
    expect(view.result.current.connection).toMatchObject({
      status: "reconnecting",
      attempt: 2,
    })

    act(() => stream.onConnected())
    expect(view.result.current.connection).toEqual({ status: "live" })
  })

  it("clears reconnect state when the stream gives up", () => {
    vi.useFakeTimers()
    const view = render()

    act(() => lastStream().onReconnect({ attempt: 12, delayMs: 300_000 }))
    act(() => vi.advanceTimersByTime(3_000))
    expect(view.result.current.connection.status).toBe("reconnecting")

    act(() => mocks.threadErrors[0]?.(new Error("stream closed")))
    expect(view.result.current.connection).toEqual({ status: "live" })
  })
})
