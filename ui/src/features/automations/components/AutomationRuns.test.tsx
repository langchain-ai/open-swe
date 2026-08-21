/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationRuns } from "./AutomationRuns"
import type { AgentThread } from "@/features/agents/lib/types"
import { useThreadsPage } from "@/features/agents/lib/queries"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useThreadsPage: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AutomationRuns", () => {
  it("shows a retry action when loading run history fails", () => {
    const refetch = vi.fn()
    vi.mocked(useThreadsPage).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isFetching: false,
      refetch,
    } as unknown as ReturnType<typeof useThreadsPage>)

    render(<AutomationRuns />)

    expect(
      screen.getByText("Automation runs could not be loaded.")
    ).toBeTruthy()
    expect(screen.queryByText("No automation runs yet.")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it("shows which automation runs posted an action to Slack", () => {
    const run = {
      id: "run-posted",
      title: "Scheduled: Dependency check",
      repo: "open-swe",
      repoFullName: "langchain-ai/open-swe",
      branch: "main",
      model: "Default",
      source: "schedule",
      threadCategory: "automation",
      triggerKind: "schedule",
      automationId: "dependency-check",
      automationName: "Dependency check",
      status: "finished",
      viewed: true,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
      automationActionPosted: true,
    } satisfies AgentThread
    vi.mocked(useThreadsPage).mockReturnValue({
      data: {
        items: [
          run,
          {
            ...run,
            id: "run-read-only",
            title: "Scheduled: Read-only check",
            automationActionPosted: false,
          },
        ],
        hasMore: false,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useThreadsPage>)

    render(<AutomationRuns />)

    expect(screen.getAllByLabelText("Action posted to Slack")).toHaveLength(1)
    expect(screen.getByText("Posted to Slack")).toBeTruthy()
  })
})
