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

const RUN = {
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

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AutomationRuns", () => {
  it.each([undefined, 10])(
    "pages through run history with limit %s",
    (limit) => {
      const pageSize = limit ?? 25
      let failOlderPage = false
      vi.mocked(useThreadsPage).mockImplementation(
        ({ offset = 0 }) =>
          ({
            data:
              failOlderPage && offset > 0
                ? undefined
                : {
                    items: [
                      {
                        ...RUN,
                        title: offset === 0 ? "Newest run" : "Older run",
                      },
                    ],
                    hasMore: offset === 0,
                  },
            isLoading: false,
            isError: failOlderPage && offset > 0,
            isFetching: false,
          }) as ReturnType<typeof useThreadsPage>
      )

      const { rerender } = render(
        <AutomationRuns limit={limit} automationId="dependency-check" />
      )
      expect(screen.getByText("Newest run")).toBeTruthy()
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Previous" })
          .disabled
      ).toBe(true)
      expect(useThreadsPage).toHaveBeenLastCalledWith({
        limit: pageSize,
        offset: 0,
        scope: "automation",
        automationId: "dependency-check",
      })

      fireEvent.click(screen.getByRole("button", { name: "Next" }))
      expect(screen.getByText("Older run")).toBeTruthy()
      expect(screen.queryByText("Newest run")).toBeNull()
      expect(screen.getByText("Page 2")).toBeTruthy()
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Next" }).disabled
      ).toBe(true)
      expect(useThreadsPage).toHaveBeenLastCalledWith({
        limit: pageSize,
        offset: pageSize,
        scope: "automation",
        automationId: "dependency-check",
      })

      failOlderPage = true
      rerender(<AutomationRuns limit={limit} automationId="dependency-check" />)
      expect(
        screen.getByText("Automation runs could not be loaded.")
      ).toBeTruthy()
      fireEvent.click(screen.getByRole("button", { name: "Previous" }))
      expect(screen.getByText("Newest run")).toBeTruthy()
      expect(screen.getByText("Page 1")).toBeTruthy()
    }
  )

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
    vi.mocked(useThreadsPage).mockReturnValue({
      data: {
        items: [
          RUN,
          {
            ...RUN,
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
