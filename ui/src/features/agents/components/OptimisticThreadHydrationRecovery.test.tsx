/** @vitest-environment jsdom */

import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { OptimisticThreadHydrationRecovery } from "./OptimisticThreadHydrationRecovery"

const mocks = vi.hoisted(() => ({
  controller: { hydrate: vi.fn().mockResolvedValue(undefined) },
  streamController: Symbol("stream-controller"),
  stream: {
    hydrationPromise: Promise.resolve(),
    messages: [] as Array<unknown>,
  },
}))

vi.mock("@langchain/react", () => ({
  STREAM_CONTROLLER: mocks.streamController,
}))

vi.mock("@/features/agents/lib/AgentThreadStreamProvider", () => ({
  useAgentThreadRuntime: () => ({
    ...mocks.stream,
    [mocks.streamController]: mocks.controller,
  }),
}))

beforeEach(() => {
  vi.useFakeTimers()
  mocks.controller.hydrate.mockClear()
  mocks.stream.hydrationPromise = Promise.resolve()
  mocks.stream.messages = []
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("OptimisticThreadHydrationRecovery", () => {
  it("retries hydration after an optimistic thread races server creation", async () => {
    mocks.stream.hydrationPromise = Promise.reject(new Error("not found"))

    render(
      <OptimisticThreadHydrationRecovery threadId="thread-1" enabled={true} />
    )

    await act(async () => {
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(250)
    })

    expect(mocks.controller.hydrate).toHaveBeenCalledWith("thread-1")
  })

  it("stops retrying once the stream contains messages", async () => {
    const view = render(
      <OptimisticThreadHydrationRecovery threadId="thread-1" enabled={true} />
    )

    await act(async () => {
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(250)
    })
    mocks.stream.messages = [{}]
    view.rerender(
      <OptimisticThreadHydrationRecovery threadId="thread-1" enabled={true} />
    )

    await act(async () => {
      await vi.runAllTimersAsync()
    })
    expect(mocks.controller.hydrate).toHaveBeenCalledTimes(1)
  })

  it("does not retry hydration for a non-optimistic thread", async () => {
    render(
      <OptimisticThreadHydrationRecovery threadId="thread-1" enabled={false} />
    )

    await act(async () => {
      await vi.runAllTimersAsync()
    })
    expect(mocks.controller.hydrate).not.toHaveBeenCalled()
  })
})
