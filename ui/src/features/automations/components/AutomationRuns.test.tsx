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
})
