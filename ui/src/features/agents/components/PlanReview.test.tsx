/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { PlanReview } from "./PlanReview"
import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  addPlanComment: vi.fn(),
  getPlanComments: vi.fn(),
  deletePlanComment: vi.fn(),
  rejectPlan: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock("@/lib/plan", () => ({
  addPlanComment: mocks.addPlanComment,
  approvePlan: vi.fn(),
  deletePlanComment: mocks.deletePlanComment,
  getPlanComments: mocks.getPlanComments,
  rejectPlan: mocks.rejectPlan,
}))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({
    html,
    className,
    onTextSelected,
  }: {
    html: string
    className?: string
    onTextSelected?: (anchor: PlanTextAnchor) => void
  }) => (
    <div data-testid="plan-artifact-frame" className={className}>
      {html}
      <button
        type="button"
        onClick={() =>
          onTextSelected?.({
            exact: "Plan",
            prefix: "",
            suffix: " details",
            start: 0,
            end: 4,
          })
        }
      >
        Select text
      </button>
    </div>
  ),
}))
vi.mock("@/features/agents/components/chat/Markdown", () => ({
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

const comment: PlanComment = {
  id: "comment-1",
  author: "Alice",
  author_login: "alice",
  body: "Clarify this step",
  created_at: "2026-08-25T12:00:00Z",
  anchor: {
    exact: "Plan",
    prefix: "",
    suffix: " details",
    start: 0,
    end: 4,
  },
}

beforeEach(() => {
  mocks.getPlanComments.mockResolvedValue([])
  mocks.addPlanComment.mockResolvedValue(comment)
  mocks.deletePlanComment.mockResolvedValue({ ok: true })
  mocks.rejectPlan.mockResolvedValue({ status: "revising" })
})

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
    expect(screen.getByTestId("plan-comments")).toBeTruthy()
  })

  it("adds a comment anchored to selected preview text", async () => {
    render(<PlanReview plan={plan} />)

    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    expect(screen.getByTestId("comment-composer").textContent).toContain("Plan")
    fireEvent.change(screen.getByTestId("comment-input"), {
      target: { value: "Clarify this step" },
    })
    fireEvent.click(screen.getByTestId("comment-submit"))

    await waitFor(() =>
      expect(mocks.addPlanComment).toHaveBeenCalledWith(
        "thread-1",
        "Clarify this step",
        {
          exact: "Plan",
          prefix: "",
          suffix: " details",
          start: 0,
          end: 4,
        }
      )
    )
    expect((await screen.findByTestId("plan-comment")).textContent).toContain(
      "Clarify this step"
    )
  })

  it("returns unanchored feedback to the conversation", async () => {
    render(<PlanReview plan={plan} />)

    fireEvent.click(screen.getByRole("button", { name: "Request changes" }))

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: "/agents/$threadId",
        params: { threadId: "thread-1" },
        search: { feedback: true },
      })
    )
    expect(mocks.rejectPlan).not.toHaveBeenCalled()
  })

  it("sends anchored feedback when requesting changes", async () => {
    mocks.getPlanComments.mockResolvedValue([comment])
    render(<PlanReview plan={plan} />)

    const requestChanges = screen.getByRole("button", {
      name: "Request changes",
    })
    await waitFor(() =>
      expect((requestChanges as HTMLButtonElement).disabled).toBe(false)
    )
    fireEvent.click(requestChanges)

    await waitFor(() =>
      expect(mocks.rejectPlan).toHaveBeenCalledWith("thread-1")
    )
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/agents/$threadId",
      params: { threadId: "thread-1" },
    })
  })
})
