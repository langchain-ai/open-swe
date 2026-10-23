/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentStreamProvider, useAgentStream } from "./AgentStreamProvider"
import { STALL_QUIET_MS, useStreamPool } from "./streamPool"
import type { DesktopNetworkResumeStall } from "@/desktop"
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
  progressEvent: (_event: unknown) => {},
  loading: false,
}))

vi.mock("@langchain/react", () => ({
  useChannelEffect: (
    _stream: unknown,
    channels: Array<string>,
    options: { onEvent: (event: unknown) => void }
  ) => {
    // The provider also subscribes lifecycle/messages for perf tracking.
    if (channels.includes("custom")) mocks.onEvent = options.onEvent
    if (channels.includes("updates")) mocks.progressEvent = options.onEvent
  },
  useStream: (options: StreamOptions) => {
    mocks.streams.push(options)
    return {
      threadId: options.threadId,
      // A getter so a test can make the stream look busy without a re-render.
      get isLoading() {
        return mocks.loading
      },
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
  createDashboardClient: () => ({}),
  createLocalGraphClient: () => ({}),
  dashboardFetch: fetch,
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

function installStallBridge(stall: (input: { threadId: string }) => Promise<DesktopNetworkResumeStall>) {
  const bridge = { networkResume: { stall } }
  Object.defineProperty(window, "openSweDesktop", {
    value: bridge,
    configurable: true,
  })
  return () => {
    Reflect.deleteProperty(window, "openSweDesktop")
  }
}

beforeEach(() => {
  mocks.streams.length = 0
  mocks.threadErrors.length = 0
  mocks.loading = false
  Reflect.deleteProperty(window, "openSweDesktop")
  useStreamPool.setState({
    entries: [],
    handles: {},
    activeId: null,
    binding: null,
    createdThreadId: null,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("AgentStreamProvider", () => {
  it("tracks only root offloading events and clears on completion", () => {
    const view = render(
      wrapper(
        <AgentStreamProvider threadId="one">
          <OffloadingProbe />
        </AgentStreamProvider>
      )
    )
    const emit = (status: string, namespace: string[] = []) =>
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
    expect(view.container.textContent).toBe("idle")
    for (const status of ["completed", "skipped", "failed"]) {
      emit("started")
      expect(view.container.textContent).toBe("offloading")
      emit(status)
      expect(view.container.textContent).toBe("idle")
    }
    emit("started")
    act(() => mocks.streams.at(-1)?.onCompleted({ reason: "success" }))
    expect(view.container.textContent).toBe("idle")
  })

  it("clears reconnect state when the stream gives up", () => {
    vi.useFakeTimers()
    render(
      wrapper(
        <AgentStreamProvider threadId="one">
          <Probe />
        </AgentStreamProvider>
      )
    )
    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")

    act(() => stream.onReconnect({ attempt: 12, delayMs: 300_000 }))
    act(() => vi.advanceTimersByTime(3_000))
    expect(useStreamPool.getState().entries[0]?.connection.status).toBe(
      "reconnecting"
    )

    act(() => mocks.threadErrors[0]?.(new Error("stream closed")))
    expect(useStreamPool.getState().entries[0]?.connection).toEqual({
      status: "live",
    })
  })

  it("waits before surfacing reconnect attempts and clears brief interruptions", () => {
    vi.useFakeTimers()
    render(
      wrapper(
        <AgentStreamProvider threadId="one">
          <Probe />
        </AgentStreamProvider>
      )
    )
    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")

    expect(stream.maxReconnectAttempts).toBe(12)
    expect(stream.reconnectDelayMs(12)).toBe(300_000)
    act(() => stream.onReconnect({ attempt: 1, delayMs: 1_000 }))
    act(() => vi.advanceTimersByTime(2_000))
    act(() => stream.onConnected())
    act(() => vi.advanceTimersByTime(1_000))
    expect(useStreamPool.getState().entries[0]?.connection).toEqual({
      status: "live",
    })

    act(() => stream.onReconnect({ attempt: 2, delayMs: 2_000 }))
    act(() => vi.advanceTimersByTime(3_000))
    expect(useStreamPool.getState().entries[0]?.connection).toMatchObject({
      status: "reconnecting",
      attempt: 2,
    })

    act(() => stream.onConnected())
    expect(useStreamPool.getState().entries[0]?.connection).toEqual({
      status: "live",
    })
  })

  it("serves the stream bound to the requested thread", () => {
    const view = render(
      wrapper(
        <AgentStreamProvider threadId="one">
          <Probe />
        </AgentStreamProvider>
      )
    )
    expect(view.container.textContent).toBe("one")

    view.rerender(
      wrapper(
        <AgentStreamProvider threadId="two">
          <Probe />
        </AgentStreamProvider>
      )
    )
    expect(view.container.textContent).toBe("two")
    expect(useStreamPool.getState().entries).toHaveLength(2)
  })

  it("announces a lazy cloud thread only once the server accepts its run", () => {
    const onThreadCreated = vi.fn()
    render(
      wrapper(
        <AgentStreamProvider threadId={null} onThreadCreated={onThreadCreated}>
          <Probe />
        </AgentStreamProvider>
      )
    )

    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")
    act(() => stream.onThreadId("created"))
    expect(onThreadCreated).not.toHaveBeenCalled()

    act(() => stream.onCreated())
    expect(onThreadCreated).toHaveBeenCalledWith("created")
  })

  it("does not treat a follow-up run on an existing thread as a creation", () => {
    const onThreadCreated = vi.fn()
    render(
      wrapper(
        <AgentStreamProvider
          threadId="existing"
          onThreadCreated={onThreadCreated}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )

    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")
    act(() => stream.onCreated())

    expect(onThreadCreated).not.toHaveBeenCalled()
  })

  it("does not announce a lazy thread the user has already left", () => {
    const onThreadCreated = vi.fn()
    const view = render(
      wrapper(
        <AgentStreamProvider threadId={null} onThreadCreated={onThreadCreated}>
          <Probe />
        </AgentStreamProvider>
      )
    )
    const draft = mocks.streams[0]
    if (!draft) throw new Error("stream was not mounted")
    act(() => draft.onThreadId("created"))

    view.rerender(
      wrapper(
        <AgentStreamProvider threadId="other" onThreadCreated={onThreadCreated}>
          <Probe />
        </AgentStreamProvider>
      )
    )
    act(() => draft.onCreated())

    expect(onThreadCreated).not.toHaveBeenCalled()
  })

  it("never announces local threads", () => {
    const onThreadCreated = vi.fn()
    render(
      wrapper(
        <AgentStreamProvider
          threadId={null}
          transport="local"
          onThreadCreated={onThreadCreated}
        >
          <Probe />
        </AgentStreamProvider>
      )
    )

    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")
    act(() => stream.onThreadId("created"))
    act(() => stream.onCreated())

    expect(onThreadCreated).not.toHaveBeenCalled()
  })

  it("clears a failed connection once a new run is accepted", () => {
    render(
      wrapper(
        <AgentStreamProvider threadId="one" transport="local">
          <Probe />
        </AgentStreamProvider>
      )
    )
    const stream = mocks.streams[0]
    if (!stream) throw new Error("stream was not mounted")

    act(() =>
      useStreamPool.getState().resumeConnection("one", {
        status: "failed",
        message: "gave up",
      })
    )
    expect(useStreamPool.getState().entries[0]?.connection.status).toBe("failed")

    act(() => stream.onCreated())

    expect(useStreamPool.getState().entries[0]?.connection).toEqual({
      status: "live",
    })
  })

  describe("silent local streams", () => {
    it("reconnects the stream when the backend says the run is gone", async () => {
      vi.useFakeTimers()
      const stall = vi.fn(async () => ({ action: "resync" }) as const)
      installStallBridge(stall)
      render(
        wrapper(
          <AgentStreamProvider threadId="one" transport="local">
            <Probe />
          </AgentStreamProvider>
        )
      )
      const entry = useStreamPool.getState().entries[0]
      if (!entry) throw new Error("stream was not mounted")

      mocks.loading = true
      await act(async () => {
        vi.advanceTimersByTime(STALL_QUIET_MS + 5_000)
      })

      expect(stall).toHaveBeenCalledWith({ threadId: "one" })
      const refreshed = useStreamPool
        .getState()
        .entries.find((candidate) => candidate.id === entry.id)
      expect(refreshed?.reconnectEpoch).toBe(1)
    })

    it("leaves a stream that keeps producing events alone", () => {
      vi.useFakeTimers()
      const stall = vi.fn(async () => ({ action: "none" }) as const)
      installStallBridge(stall)
      render(
        wrapper(
          <AgentStreamProvider threadId="one" transport="local">
            <Probe />
          </AgentStreamProvider>
        )
      )
      mocks.loading = true

      for (let index = 0; index < 6; index += 1) {
        act(() => vi.advanceTimersByTime(20_000))
        act(() => mocks.progressEvent({ method: "updates" }))
      }

      expect(stall).not.toHaveBeenCalled()
    })
  })

  describe("local backend process loss", () => {
    function installBackendLostBridge() {
      const arm = vi.fn(async () => null)
      let lost: (() => void) | undefined
      const onBackendLost = vi.fn((listener: () => void) => {
        lost = listener
        return () => {}
      })
      Object.defineProperty(window, "openSweDesktop", {
        value: { networkResume: { arm, onBackendLost } },
        configurable: true,
      })
      return {
        arm,
        onBackendLost,
        loseBackend: () => lost?.(),
        setLoading: (value: boolean) => {
          mocks.loading = value
        },
      }
    }

    it("arms a loading local thread as soon as the backend exits", () => {
      const bridge = installBackendLostBridge()
      render(
        wrapper(
          <AgentStreamProvider threadId="one" transport="local">
            <Probe />
          </AgentStreamProvider>
        )
      )
      bridge.setLoading(true)

      act(() => bridge.loseBackend())

      expect(bridge.onBackendLost).toHaveBeenCalledTimes(1)
      expect(bridge.arm).toHaveBeenCalledTimes(1)
      expect(bridge.arm).toHaveBeenCalledWith({
        threadId: "one",
        reason: "backend-down",
      })
    })

    it("leaves a local thread with no run in flight alone", () => {
      const bridge = installBackendLostBridge()
      render(
        wrapper(
          <AgentStreamProvider threadId="one" transport="local">
            <Probe />
          </AgentStreamProvider>
        )
      )
      bridge.setLoading(false)

      act(() => bridge.loseBackend())

      expect(bridge.arm).not.toHaveBeenCalled()
    })

    it("does not listen for backend loss on cloud streams", () => {
      const bridge = installBackendLostBridge()
      render(
        wrapper(
          <AgentStreamProvider threadId="one">
            <Probe />
          </AgentStreamProvider>
        )
      )

      expect(bridge.onBackendLost).not.toHaveBeenCalled()
    })
  })
})
