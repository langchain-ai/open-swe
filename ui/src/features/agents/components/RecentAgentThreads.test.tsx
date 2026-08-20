/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RecentAgentThreads } from "./RecentAgentThreads"
import type { ReactNode } from "react"

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
  AgentThreadPage: ({
    threadId,
    active,
  }: {
    threadId: string
    active: boolean
  }) => <div data-active={active}>thread {threadId}</div>,
}))

afterEach(cleanup)

describe("RecentAgentThreads", () => {
  it("uses the layout stream for the active thread", () => {
    render(<RecentAgentThreads activeThreadId="one" />)

    expect(screen.getByText("thread one").closest("[data-provider]")).toBeNull()
  })

  it("keeps the three most recently viewed threads mounted", () => {
    const view = render(<RecentAgentThreads activeThreadId="one" />)

    act(() => view.rerender(<RecentAgentThreads activeThreadId="two" />))
    expect(screen.getByText("thread one").dataset.active).toBe("false")
    expect(screen.getByText("thread two").dataset.active).toBe("true")

    act(() => view.rerender(<RecentAgentThreads activeThreadId="three" />))
    act(() => view.rerender(<RecentAgentThreads activeThreadId="four" />))

    expect(screen.queryByText("thread one")).toBeNull()
    expect(screen.getByText("thread two")).toBeTruthy()
    expect(screen.getByText("thread three")).toBeTruthy()
    expect(screen.getByText("thread four")).toBeTruthy()
  })
})
