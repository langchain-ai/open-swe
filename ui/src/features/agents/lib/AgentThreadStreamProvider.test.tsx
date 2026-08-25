/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AgentThreadStreamProvider } from "./AgentThreadStreamProvider"
import type { ReactNode } from "react"

const mocks = vi.hoisted(() => ({
  controller: { hydrate: vi.fn() },
  streamController: Symbol("stream-controller"),
  streamProviderProps: vi.fn(),
}))

vi.mock("@langchain/langgraph-sdk", () => ({
  Client: class Client {},
  overrideFetchImplementation: vi.fn(),
}))

vi.mock("@langchain/react", () => ({
  STREAM_CONTROLLER: mocks.streamController,
  StreamProvider: (props: { children: ReactNode; onThreadId?: unknown }) => {
    mocks.streamProviderProps(props)
    return props.children
  },
  useStreamContext: () => ({
    [mocks.streamController]: mocks.controller,
  }),
}))

afterEach(() => {
  cleanup()
  mocks.controller.hydrate.mockClear()
  mocks.streamProviderProps.mockClear()
  vi.restoreAllMocks()
})

describe("AgentThreadStreamProvider", () => {
  it("forwards new thread ids to the latest callback", () => {
    const queryClient = new QueryClient()
    const firstCallback = vi.fn()
    const latestCallback = vi.fn()

    const view = render(
      <QueryClientProvider client={queryClient}>
        <AgentThreadStreamProvider threadId="existing" onThreadId={firstCallback}>
          <div>thread</div>
        </AgentThreadStreamProvider>
      </QueryClientProvider>
    )
    const capturedCallback = mocks.streamProviderProps.mock.calls[0]?.[0]
      .onThreadId as (threadId: string) => void

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <AgentThreadStreamProvider threadId={null} onThreadId={latestCallback}>
          <div>thread</div>
        </AgentThreadStreamProvider>
      </QueryClientProvider>
    )
    capturedCallback("new-thread")

    expect(firstCallback).not.toHaveBeenCalled()
    expect(latestCallback).toHaveBeenCalledWith("new-thread")
  })

  it("does not rehydrate the thread on foreground", () => {
    const queryClient = new QueryClient()

    render(
      <QueryClientProvider client={queryClient}>
        <AgentThreadStreamProvider threadId="thread-1">
          <div>thread</div>
        </AgentThreadStreamProvider>
      </QueryClientProvider>
    )

    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    act(() => document.dispatchEvent(new Event("visibilitychange")))

    expect(screen.getByText("thread")).toBeTruthy()
    expect(mocks.controller.hydrate).not.toHaveBeenCalled()
  })
})
