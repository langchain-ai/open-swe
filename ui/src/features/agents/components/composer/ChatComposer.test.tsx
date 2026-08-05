/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { buildCommandItems, ChatComposer } from "./ChatComposer"
import { replaceTextRange } from "./composerTrigger"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"

const stream = {
  isLoading: false,
  threadId: "thread-1",
  stop: vi.fn(),
  disconnect: vi.fn(),
}

vi.mock("@langchain/react", () => ({
  useStreamContext: () => stream,
}))

const cancelThread = vi.fn(async (threadId: string) => ({
  id: threadId,
  status: "interrupted",
}))

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: { cancelThread: (threadId: string) => cancelThread(threadId) },
  AgentsApiError: class AgentsApiError extends Error {},
}))

afterEach(() => cleanup())

beforeEach(() => {
  stream.isLoading = false
  stream.stop.mockClear()
  stream.disconnect.mockClear()
  cancelThread.mockClear()
})

function renderComposer(running: boolean) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AgentThreadStreamBoundary>
        <ChatComposer activeRun={{ threadId: "thread-1", running }} />
      </AgentThreadStreamBoundary>
    </QueryClientProvider>
  )
}

describe("ChatComposer stop button", () => {
  it("offers to stop a run this client never joined", async () => {
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
    expect(stream.disconnect).toHaveBeenCalled()
  })

  it("cancels server-side even while streaming, since stop() may know no run id", async () => {
    stream.isLoading = true
    renderComposer(false)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
  })

  it("keeps the run live when cancellation fails", async () => {
    cancelThread.mockRejectedValueOnce(new Error("502"))
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalled())
    // No false "stopped" state: the stream stays connected so status polling
    // (which only runs while the cached status is `running`) keeps going.
    expect(stream.disconnect).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
  })

  it("shows the send button when no run is live", () => {
    renderComposer(false)

    expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Stop run" })).toBeNull()
  })
})

describe("ChatComposer skill autocomplete", () => {
  it("offers a matching skill and preserves surrounding prompt text", () => {
    const trigger = {
      kind: "slash-command" as const,
      query: "review",
      rangeStart: 7,
      rangeEnd: 14,
    }
    const [skill] = buildCommandItems(trigger, [], [
      {
        name: "review-pr",
        description: "Review a pull request",
        instructions: "",
      },
    ])

    expect(skill).toMatchObject({ type: "skill", name: "review-pr" })
    expect(replaceTextRange("Please /review handle this", 7, 14, "/review-pr ").text).toBe(
      "Please /review-pr  handle this"
    )
  })
})
