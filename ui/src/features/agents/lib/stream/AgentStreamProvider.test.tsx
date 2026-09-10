/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentStreamProvider, useAgentStream } from "./AgentStreamProvider"
import { useStreamPool } from "./streamPool"
import type { ReactNode } from "react"

interface StreamOptions {
  threadId: string | null
  onThreadId: (threadId: string) => void
  onCreated: () => void
  onCompleted: () => void
}

const mocks = vi.hoisted(() => ({
  streams: [] as Array<StreamOptions>,
}))

vi.mock("@langchain/react", () => ({
  useStream: (options: StreamOptions) => {
    mocks.streams.push(options)
    return { threadId: options.threadId, isLoading: false }
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

function wrapper(children: ReactNode) {
  const client = new QueryClient()
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  mocks.streams.length = 0
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
})

describe("AgentStreamProvider", () => {
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

  it("remounts a kicked thread's stream while keeping it served", () => {
    const view = render(
      wrapper(
        <AgentStreamProvider threadId="one">
          <Probe />
        </AgentStreamProvider>
      )
    )
    expect(mocks.streams).toHaveLength(1)

    act(() => useStreamPool.getState().kick("cloud", "one"))

    expect(mocks.streams).toHaveLength(2)
    expect(mocks.streams[1]?.threadId).toBe("one")
    expect(view.container.textContent).toBe("one")
    expect(useStreamPool.getState().entries).toHaveLength(1)
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
})
