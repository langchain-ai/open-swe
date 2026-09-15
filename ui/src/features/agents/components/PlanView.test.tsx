/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { PlanView } from "./PlanView"
import type { PlanData } from "@/lib/plan"

const mocks = vi.hoisted(() => ({ useQuery: vi.fn() }))

vi.mock("@tanstack/react-query", () => ({ useQuery: mocks.useQuery }))
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock("@/lib/hydration", () => ({ useIsHydrated: () => true }))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({ className }: { className?: string }) => (
    <iframe data-testid="plan-artifact-frame" className={className} />
  ),
}))
vi.mock("@/features/agents/components/PlanReview", () => ({
  PlanReview: () => <div data-testid="plan-review" />,
}))

const plan: PlanData = {
  threadId: "thread-1",
  status: "ready",
  html: "<h1>Plan</h1>",
  markdown: "",
  approvedBy: null,
  approvedAt: null,
  user: {
    id: "user-1",
    login: "alice",
    email: null,
    name: "Alice",
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PlanView", () => {
  it("fills the standalone view with the HTML artifact below a compact back bar", () => {
    mocks.useQuery.mockReturnValue({
      data: plan,
      isLoading: false,
      isError: false,
    })

    render(<PlanView threadId="thread-1" standalone />)

    const backLink = screen.getByText("Back to conversation")
    const backBar = backLink.closest("nav")
    const artifact = screen.getByTestId("plan-artifact-frame")

    expect(backBar?.className).toContain("h-9")
    expect(artifact.className).toContain("flex-1")
    expect(screen.queryByTestId("plan-review")).toBeNull()
  })

  it("keeps the review controls for embedded plans", () => {
    mocks.useQuery.mockReturnValue({
      data: plan,
      isLoading: false,
      isError: false,
    })

    render(<PlanView threadId="thread-1" />)

    expect(screen.getByTestId("plan-review")).toBeTruthy()
    expect(screen.queryByText("Back to conversation")).toBeNull()
  })
})
