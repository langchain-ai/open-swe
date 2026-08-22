/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { PlanReview } from "./PlanReview"
import type { PlanData } from "@/lib/plan"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock("@/lib/plan", () => ({
  approvePlan: vi.fn(),
}))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({
    html,
    className,
  }: {
    html: string
    className?: string
  }) => (
    <div data-testid="plan-artifact-frame" className={className}>
      {html}
    </div>
  ),
}))
vi.mock("@/components/markdown/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

const plan: PlanData = {
  threadId: "thread-1",
  status: "ready",
  html: "<h1>Plan</h1>",
  markdown: "",
  isOwner: true,
  approvedBy: null,
  approvedAt: null,
  user: {
    id: "user-1",
    login: "alice",
    email: "alice@example.com",
    name: "Alice",
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PlanReview", () => {
  it("uses the available viewport for the plan artifact", () => {
    render(<PlanReview plan={plan} />)

    const review = screen.getByTestId("plan-review")
    const layout = review.firstElementChild as HTMLElement
    const document = screen.getByTestId("plan-document")
    const artifact = screen.getByTestId("plan-artifact-frame")

    expect(review.className).toContain("overflow-hidden")
    expect(layout.className).toContain("w-full")
    expect(layout.className).not.toContain("max-w-")
    expect(document.className).toContain("flex-1")
    expect(artifact.className).toContain("h-full")
  })

  it("returns request-change feedback to the conversation", async () => {
    render(<PlanReview plan={plan} />)

    expect(screen.queryByTestId("edit-plan")).toBeNull()
    expect(screen.queryByTestId("plan-editor")).toBeNull()
    expect(screen.queryByTestId("plan-comments")).toBeNull()

    const requestChanges = screen.getByRole("button", {
      name: "Request changes",
    })
    expect((requestChanges as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(requestChanges)

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: "/agents/$threadId",
        params: { threadId: "thread-1" },
        search: { feedback: true },
      })
    )
  })
})
