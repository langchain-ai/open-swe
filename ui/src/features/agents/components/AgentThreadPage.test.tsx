/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query"
import { useState, type ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AgentThreadPage } from "./AgentThreadPage"
import { LoadError } from "@/components/LoadError"
import { agentThreadKeys, useAgentThread } from "@/features/agents/lib/queries"

const messageLoad = vi.hoisted(() => ({ failed: false }))

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
vi.mock("@/features/agents/lib/threadSource/ThreadSourceProvider", () => ({
  ThreadSourceProvider: ({ children }: { children: React.ReactNode }) =>
    children,
}))

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
)

const threadQuery = {
  data: { id: "thread-1", title: "Fix web title" },
  isLoading: false,
  isError: false,
}

afterEach(() => {
  cleanup()
  messageLoad.failed = false
  document.title = "Robin"
})

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

  it("does not update the title for an inactive cached thread", () => {
    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)
    document.title = "Current thread · Robin"

    render(<AgentThreadPage threadId="thread-1" active={false} />, { wrapper })

    expect(document.title).toBe("Current thread · Robin")
  })
})
