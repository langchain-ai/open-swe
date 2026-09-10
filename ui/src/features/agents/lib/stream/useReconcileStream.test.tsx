/** @vitest-environment jsdom */

import { cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { RECONCILE_GRACE_MS, useReconcileStream } from "./useReconcileStream"
import { useStreamPool } from "./streamPool"
import type { AgentStream } from "./streamPool"

const mocks = vi.hoisted(() => ({
  stream: {} as Partial<AgentStream>,
}))

vi.mock("./AgentStreamProvider", () => ({
  useAgentStream: () => mocks.stream,
}))

function setStream(stream: Partial<AgentStream>) {
  mocks.stream = { threadId: "t", isThreadLoading: false, ...stream }
}

beforeEach(() => {
  vi.useFakeTimers()
  useStreamPool.setState({
    entries: [],
    handles: {},
    activeId: null,
    binding: null,
    createdThreadId: null,
  })
  useStreamPool.getState().activate("cloud", "t")
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function generation() {
  return useStreamPool.getState().entries[0]?.generation
}

describe("useReconcileStream", () => {
  it("kicks once when the server runs but the stream never joined", () => {
    setStream({ isLoading: false })
    renderHook(() => useReconcileStream("t", true))

    vi.advanceTimersByTime(RECONCILE_GRACE_MS - 1)
    expect(generation()).toBe(0)

    vi.advanceTimersByTime(1)
    expect(generation()).toBe(1)

    vi.advanceTimersByTime(RECONCILE_GRACE_MS * 3)
    expect(generation()).toBe(1)
  })

  it("kicks when the stream is stuck loading after the server went idle", () => {
    setStream({ isLoading: true })
    renderHook(() => useReconcileStream("t", false))

    vi.advanceTimersByTime(RECONCILE_GRACE_MS)
    expect(generation()).toBe(1)
  })

  it("does nothing when the disagreement resolves within the grace period", () => {
    setStream({ isLoading: false })
    const hook = renderHook(({ running }) => useReconcileStream("t", running), {
      initialProps: { running: true },
    })

    vi.advanceTimersByTime(RECONCILE_GRACE_MS / 2)
    setStream({ isLoading: true })
    hook.rerender({ running: true })

    vi.advanceTimersByTime(RECONCILE_GRACE_MS * 2)
    expect(generation()).toBe(0)
  })

  it("leaves a still-hydrating instance alone", () => {
    setStream({ isLoading: false, isThreadLoading: true })
    renderHook(() => useReconcileStream("t", true))

    vi.advanceTimersByTime(RECONCILE_GRACE_MS * 2)
    expect(generation()).toBe(0)
  })
})
