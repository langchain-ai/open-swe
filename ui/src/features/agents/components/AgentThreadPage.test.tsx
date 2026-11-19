/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query"
import { useState, type ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgentThreadPage } from "./AgentThreadPage"
import { LoadError } from "@/components/LoadError"
import { agentThreadKeys, useAgentThread } from "@/features/agents/lib/queries"
import { fetchTranscript } from "@/features/agents/lib/transcript/api"
import type { Message } from "@/features/agents/lib/types"
import { setUseStreamPreference } from "@/lib/streamPreference"

const messageLoad = vi.hoisted(() => ({ failed: false }))
const stream = vi.hoisted(() => ({
  messages: [] as Array<Message>,
  shownFrom: 0,
  mounts: 0,
}))

vi.mock("@/features/agents/components/AgentThreadView", () => ({
  AgentThreadView: ({
    thread,
    retry,
  }: {
    thread: { title: string }
    retry: () => void
  }) => {
    const [failed] = useState(messageLoad.failed)
    return failed ? (
      <LoadError
        title="Unable to load messages"
        error="Transcript request failed"
        retry={retry}
      />
    ) : (
      <p>{thread.title}</p>
    )
  },
}))
vi.mock("@/features/agents/lib/provider/useIsInAgentThreadStream", () => ({
  AgentThreadStreamBoundary: ({ children }: { children: React.ReactNode }) =>
    children,
}))
vi.mock("@/features/agents/lib/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/agents/lib/queries")>()),
  useAgentThread: vi.fn(),
}))
vi.mock("@/features/agents/lib/threadSource/ThreadSourceProvider", async () => {
  const { useEffect } = await import("react")
  return {
    ThreadSourceProvider: ({ children }: { children: React.ReactNode }) => {
      useEffect(() => {
        stream.mounts += 1
      }, [])
      return <section aria-label="Thread stream">{children}</section>
    },
    useThreadSource: () => ({
      kind: "stream",
      messages: stream.messages,
      shownFrom: stream.shownFrom,
      isHydrating: false,
      isRunning: false,
      hydration: Promise.resolve(),
    }),
  }
})
vi.mock("@/features/agents/lib/transcript/api", () => ({
  fetchTranscript: vi.fn(() => new Promise(() => {})),
}))
vi.mock("@/features/agents/components/AgentPromptBar", () => ({
  AgentPromptBar: ({ disabled }: { disabled?: boolean }) => (
    <textarea aria-label="Message" disabled={disabled} />
  ),
}))
vi.mock("@/features/agents/components/messages", () => ({
  Messages: ({ messages }: { messages: Array<Message> }) =>
    messages.flatMap((message) =>
      message.chunks.flatMap((chunk) =>
        chunk.kind === "text" ? [<p key={message.id}>{chunk.text}</p>] : []
      )
    ),
}))

let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(["session"], null)
})

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
)

const threadQuery = {
  data: { id: "thread-1", title: "Fix web title" },
  isLoading: false,
  isError: false,
}

afterEach(() => {
  cleanup()
  client.clear()
  messageLoad.failed = false
  stream.messages = []
  stream.shownFrom = 0
  stream.mounts = 0
  window.localStorage.clear()
  document.title = "Robin"
})

const awaitingSummary = {
  data: undefined,
  isPending: true,
  isError: false,
}

const answer: Message = {
  id: "answer",
  author: "agent",
  timestamp: "2026-11-17T10:00:00Z",
  chunks: [{ kind: "text", text: "The title now updates on rename." }],
}

describe("AgentThreadPage", () => {
  it("keeps failed loads visible with thread diagnostics and recovery", () => {
    vi.mocked(useAgentThread).mockReturnValue({
      ...threadQuery,
      data: undefined,
      isError: true,
      error: new Error("Request failed (503)"),
    } as never)

    render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    expect(screen.getByRole("alert").textContent).toContain("thread-1")
    expect(screen.getByRole("alert").textContent).toContain(
      "Request failed (503)"
    )
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy()
  })

  it("retries a failed embedded thread without replacing the surrounding page", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new Error("Request failed (503)"))
      .mockResolvedValue(threadQuery.data)
    vi.mocked(useAgentThread).mockImplementation((threadId) => {
      return useQuery({
        queryKey: agentThreadKeys.detail(threadId),
        queryFn: request,
        retry: false,
      }) as never
    })
    document.title = "PR review · Robin"
    render(
      <>
        <input aria-label="Review draft" defaultValue="Keep this draft" />
        <AgentThreadPage threadId="thread-1" ownsTitle={false} />
      </>,
      { wrapper }
    )

    await screen.findByRole("alert")
    const draft = screen.getByRole("textbox", { name: "Review draft" })
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))

    expect(await screen.findByText("Fix web title")).toBeTruthy()
    expect(screen.queryByRole("alert")).toBeNull()
    expect(screen.getByRole("textbox", { name: "Review draft" })).toBe(draft)
    expect(document.title).toBe("PR review · Robin")
  })

  it("retries messages without discarding loaded thread details", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(threadQuery.data)
      .mockRejectedValue(new Error("Thread detail unavailable"))
    vi.mocked(useAgentThread).mockImplementation((threadId) => {
      return useQuery({
        queryKey: agentThreadKeys.detail(threadId),
        queryFn: request,
        retry: false,
        staleTime: 30_000,
      }) as never
    })
    messageLoad.failed = true
    render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    await screen.findByText("Unable to load messages")
    messageLoad.failed = false
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))

    expect(await screen.findByText("Fix web title")).toBeTruthy()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("uses the active thread title as the document title", () => {
    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)

    const view = render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    expect(document.title).toBe("Fix web title · Robin")
    view.unmount()
    expect(document.title).toBe("Agents · Robin")
  })

  it("reads the thread through the stream while its summary loads, under the SDK setting", () => {
    vi.mocked(useAgentThread).mockReturnValue(awaitingSummary as never)
    stream.messages = [answer]

    const view = render(<AgentThreadPage threadId="thread-1" />, { wrapper })
    expect(screen.queryByRole("region", { name: "Thread stream" })).toBeNull()

    act(() => setUseStreamPreference(true))
    view.rerender(<AgentThreadPage threadId="thread-1" />)

    expect(screen.getByText("The title now updates on rename.")).toBeTruthy()
    expect(
      (screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement)
        .disabled
    ).toBe(true)

    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)
    view.rerender(<AgentThreadPage threadId="thread-1" />)

    expect(screen.getByText("Fix web title")).toBeTruthy()
    expect(stream.mounts).toBe(1)
  })

  it("starts the transcript read while the summary loads, unless the SDK setting is on", () => {
    vi.mocked(useAgentThread).mockReturnValue(awaitingSummary as never)
    render(<AgentThreadPage threadId="transcript-early" />, { wrapper })
    expect(fetchTranscript).toHaveBeenCalledWith("transcript-early")

    cleanup()
    setUseStreamPreference(true)
    render(<AgentThreadPage threadId="stream-early" />, { wrapper })
    expect(fetchTranscript).not.toHaveBeenCalledWith("stream-early")
  })

  it("shows only the newest turns the stream source shows while the summary loads", () => {
    setUseStreamPreference(true)
    vi.mocked(useAgentThread).mockReturnValue(awaitingSummary as never)
    stream.messages = [
      {
        ...answer,
        id: "older",
        chunks: [{ kind: "text", text: "Older turn" }],
      },
      answer,
    ]
    stream.shownFrom = 1
    render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    expect(screen.getByText("The title now updates on rename.")).toBeTruthy()
    expect(screen.queryByText("Older turn")).toBeNull()
  })

  it("keeps reading the thread when a subagent opens before the summary arrives", () => {
    setUseStreamPreference(true)
    vi.mocked(useAgentThread).mockReturnValue(awaitingSummary as never)
    const view = render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    view.rerender(<AgentThreadPage threadId="thread-1" subagentId="call-1" />)

    expect(screen.getByRole("region", { name: "Thread stream" })).toBeTruthy()
    expect(stream.mounts).toBe(1)
  })

  it("shows a thread it cannot load as before, under the SDK setting", () => {
    setUseStreamPreference(true)
    vi.mocked(useAgentThread).mockReturnValue({
      ...awaitingSummary,
      isPending: false,
      isError: true,
      error: new Error("thread not found"),
    } as never)
    stream.messages = [answer]

    render(<AgentThreadPage threadId="thread-1" />, { wrapper })

    expect(screen.getByRole("alert").textContent).toContain("thread not found")
    expect(screen.queryByRole("region", { name: "Thread stream" })).toBeNull()
    expect(screen.queryByText("The title now updates on rename.")).toBeNull()
  })

  it("does not update the title for an inactive cached thread", () => {
    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)
    document.title = "Current thread · Robin"

    render(<AgentThreadPage threadId="thread-1" active={false} />, { wrapper })

    expect(document.title).toBe("Current thread · Robin")
  })
})
