/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationRuns } from "./AutomationRuns"
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

  it("shows whether action-only runs performed an action", () => {
    vi.mocked(useThreadsPage).mockReturnValue({
      data: {
        items: [
          {
            id: "action",
            title: "Action run",
            automationId: "schedule-1",
            automationName: "Dependency check",
            automationNotificationMode: "on_action",
            automationActionTaken: true,
            triggerKind: "schedule",
            status: "finished",
            updatedAt: 3,
          },
          {
            id: "no-action",
            title: "No action run",
            automationId: "schedule-1",
            automationName: "Dependency check",
            automationNotificationMode: "on_action",
            automationActionTaken: false,
            triggerKind: "schedule",
            status: "finished",
            updatedAt: 2,
          },
          {
            id: "pending",
            title: "Pending run",
            automationId: "schedule-1",
            automationName: "Dependency check",
            automationNotificationMode: "on_action",
            automationActionTaken: false,
            triggerKind: "schedule",
            status: "running",
            updatedAt: 1,
          },
        ],
        hasMore: false,
      },
      isLoading: false,
      isError: false,
      isFetching: false,
    } as unknown as ReturnType<typeof useThreadsPage>)

    render(<AutomationRuns automationId="schedule-1" />)

    expect(screen.getByText("Action taken")).toBeTruthy()
    expect(screen.getByText("No action")).toBeTruthy()
    expect(screen.getByText("Awaiting action")).toBeTruthy()
  })
})
