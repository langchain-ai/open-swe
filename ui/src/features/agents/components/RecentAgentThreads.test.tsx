/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RecentAgentThreads } from "./RecentAgentThreads"
import type { ReactNode } from "react"

vi.mock("@langchain/react", () => ({
  useStreamContext: () => ({ threadId: "one" }),
}))

vi.mock("@/features/agents/lib/AgentThreadStreamProvider", () => ({
  AgentThreadStreamProvider: ({
    threadId,
    children,
  }: {
    threadId: string
    children: ReactNode
  }) => <section data-provider={threadId}>{children}</section>,
}))

vi.mock("@/features/agents/components/AgentThreadPage", () => ({
  AgentThreadPage: ({ threadId }: { threadId: string }) => (
    <div>thread {threadId}</div>
  ),
}))

afterEach(cleanup)

describe("RecentAgentThreads", () => {
  it("mounts only the active thread", () => {
    const view = render(<RecentAgentThreads activeThreadId="one" />)

    act(() => view.rerender(<RecentAgentThreads activeThreadId="two" />))
    expect(screen.queryByText("thread one")).toBeNull()
    expect(screen.getByText("thread two")).toBeTruthy()

    act(() => view.rerender(<RecentAgentThreads activeThreadId="three" />))
    expect(screen.queryByText("thread two")).toBeNull()
    expect(screen.getByText("thread three")).toBeTruthy()
  })

  it("renders the layout's own thread without a second provider", () => {
    render(<RecentAgentThreads activeThreadId="one" />)
    expect(screen.getByText("thread one").closest("[data-provider]")).toBeNull()
  })

  it("gives every other thread its own provider", () => {
    render(<RecentAgentThreads activeThreadId="two" />)
    expect(screen.getByText("thread two").parentElement?.dataset.provider).toBe(
      "two"
    )
  })
})
