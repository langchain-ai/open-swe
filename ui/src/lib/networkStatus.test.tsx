// @vitest-environment jsdom

import { act, cleanup, render, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { toast } from "sonner"

import { useStreamPool } from "@/features/agents/lib/stream/streamPool"
import {
  NETWORK_OFFLINE_MESSAGE,
  NETWORK_STATUS_TOAST_ID,
  NetworkStatusWatcher,
} from "./networkStatus"
import type {
  DesktopNetworkResumeEvent,
  DesktopNetworkResumeSnapshot,
} from "@/desktop"

vi.mock("sonner", () => ({
  toast: {
    dismiss: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
  },
}))

function reconnectActiveStream() {
  useStreamPool.getState().activate("cloud", "thread-1")
  const id = useStreamPool.getState().activeId
  if (!id) throw new Error("stream was not activated")
  useStreamPool.getState().streamReconnecting(id, 2, Date.now() + 4_000)
  return id
}

function renderWatcher(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <NetworkStatusWatcher />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  useStreamPool.setState({
    entries: [],
    handles: {},
    activeId: null,
    binding: null,
    createdThreadId: null,
  })
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  Reflect.deleteProperty(window, "openSweDesktop")
})

describe("NetworkStatusWatcher", () => {
  it("keeps a notice up while offline and reconnects when the network returns", () => {
    const id = reconnectActiveStream()
    const queryClient = new QueryClient()
    const refetch = vi
      .spyOn(queryClient, "refetchQueries")
      .mockResolvedValue(undefined)
    renderWatcher(queryClient)

    act(() => {
      window.dispatchEvent(new Event("offline"))
    })

    expect(toast.info).toHaveBeenCalledWith(
      NETWORK_OFFLINE_MESSAGE,
      expect.objectContaining({
        id: NETWORK_STATUS_TOAST_ID,
        duration: Infinity,
      })
    )

    act(() => {
      window.dispatchEvent(new Event("online"))
    })

    expect(toast.dismiss).toHaveBeenCalledWith(NETWORK_STATUS_TOAST_ID)
    expect(toast.success).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ duration: 3_000 })
    )
    expect(refetch).toHaveBeenCalledWith({ type: "active" })
    const entry = useStreamPool
      .getState()
      .entries.find((candidate) => candidate.id === id)
    expect(entry?.connection).toEqual({ status: "live" })
    expect(entry?.reconnectEpoch).toBe(1)
  })

  it("renders the recovery chain's phase instead of guessing from retries", async () => {
    useStreamPool.getState().activate("local", "thread-1")
    const snapshot = (chains: DesktopNetworkResumeSnapshot["chains"]) =>
      ({
        epoch: "nr-1",
        revision: 1,
        enabled: true,
        network: { reachable: false, checkedAt: 0 },
        chains,
      }) satisfies DesktopNetworkResumeSnapshot
    const listeners: Array<(event: DesktopNetworkResumeEvent) => void> = []
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: {
        networkResume: {
          arm: async () => null,
          disarm: async () => true,
          working: async () => true,
          complete: async () => true,
          snapshot: async () =>
            snapshot([
              {
                threadId: "thread-1",
                phase: "waiting",
                reason: "backend-down",
                rootRunId: "run-1",
                previousRunId: null,
                generation: 1,
                sendTicket: 0,
                closing: false,
                firstWaitingAt: 0,
                dispatched: false,
              },
            ]),
          onChange: (listener: (event: DesktopNetworkResumeEvent) => void) => {
            listeners.push(listener)
            return () => {}
          },
        },
      },
    })
    const queryClient = new QueryClient()
    vi.spyOn(queryClient, "refetchQueries").mockResolvedValue(undefined)
    renderWatcher(queryClient)

    const entry = () =>
      useStreamPool
        .getState()
        .entries.find((candidate) => candidate.threadId === "thread-1")

    await waitFor(() =>
      expect(entry()?.connection).toEqual({
        status: "reconnecting",
        attempt: 1,
        retryAt: 0,
        source: "resume",
      })
    )
    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("Continuing automatically"),
      expect.anything()
    )

    act(() =>
      listeners[0]?.({
        snapshot: snapshot([]),
        change: {
          kind: "chain-removed",
          threadId: "thread-1",
          outcome: "timed-out",
        },
      })
    )

    expect(entry()?.connection.status).toBe("failed")
  })

  it("wakes a reconnecting stream only after the continuation run exists", () => {
    useStreamPool.getState().activate("local", "thread-1")
    useStreamPool.getState().resumeConnection("thread-1", {
      status: "reconnecting",
      attempt: 1,
      retryAt: 0,
      source: "resume",
    })
    const chain = (
      phase: "waiting" | "dispatching",
      dispatched: boolean
    ): DesktopNetworkResumeSnapshot["chains"][number] => ({
      threadId: "thread-1",
      phase,
      reason: "backend-down",
      rootRunId: "run-1",
      previousRunId: null,
      generation: 1,
      sendTicket: 1,
      closing: false,
      firstWaitingAt: 0,
      dispatched,
    })
    const snapshot = (
      value: DesktopNetworkResumeSnapshot["chains"][number]
    ): DesktopNetworkResumeSnapshot => ({
      epoch: "nr-1",
      revision: 1,
      enabled: true,
      network: { reachable: true, checkedAt: 0 },
      chains: [value],
    })
    let listener: ((event: DesktopNetworkResumeEvent) => void) | undefined
    Object.defineProperty(window, "openSweDesktop", {
      configurable: true,
      value: {
        networkResume: {
          arm: async () => null,
          disarm: async () => true,
          working: async () => true,
          complete: async () => true,
          snapshot: async () => snapshot(chain("waiting", false)),
          onChange: (callback: (event: DesktopNetworkResumeEvent) => void) => {
            listener = callback
            return () => {}
          },
        },
      },
    })
    renderWatcher(new QueryClient())
    const entry = () =>
      useStreamPool
        .getState()
        .entries.find((candidate) => candidate.threadId === "thread-1")

    // The coordinator emits `dispatching` before `resumeRun` resolves. Waking
    // here remounts the stream against a thread the backend has not recreated,
    // which parks its pump on a 404.
    act(() =>
      listener?.({
        snapshot: snapshot(chain("dispatching", false)),
        change: { kind: "chain-updated", threadId: "thread-1" },
      })
    )
    expect(entry()?.reconnectEpoch).toBe(0)
    expect(entry()?.connection.status).toBe("reconnecting")

    act(() =>
      listener?.({
        snapshot: snapshot(chain("dispatching", true)),
        change: { kind: "chain-updated", threadId: "thread-1" },
      })
    )
    expect(entry()?.reconnectEpoch).toBe(1)
    expect(entry()?.connection.status).toBe("live")
  })

  it("shows the notice immediately when the page starts offline", () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false)
    const queryClient = new QueryClient()
    vi.spyOn(queryClient, "refetchQueries").mockResolvedValue(undefined)
    renderWatcher(queryClient)

    expect(toast.info).toHaveBeenCalledWith(
      NETWORK_OFFLINE_MESSAGE,
      expect.objectContaining({ id: NETWORK_STATUS_TOAST_ID })
    )
  })
})
