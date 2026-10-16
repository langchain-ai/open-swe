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
  addPlanComment: vi.fn(),
  getPlanComments: vi.fn(),
  deletePlanComment: vi.fn(),
  submitPlanComments: vi.fn(),
  reportError: vi.fn(),
}))

vi.mock("@/lib/errorReporting", () => ({ reportError: mocks.reportError }))
vi.mock("@/lib/plan", () => ({
  addPlanComment: mocks.addPlanComment,
  deletePlanComment: mocks.deletePlanComment,
  getPlanComments: mocks.getPlanComments,
  submitPlanComments: mocks.submitPlanComments,
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
  status: "shared",
  html: "<h1>Plan</h1>",
  markdown: "",
  dismissed: false,
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
  mocks.submitPlanComments.mockResolvedValue({ status: "submitted" })
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

  it.each(["click", "metaKey", "ctrlKey"])(
    "adds an anchored comment via %s",
    async (method) => {
      render(<PlanReview plan={plan} />)

      fireEvent.click(screen.getByRole("button", { name: "Select text" }))
      expect(screen.getByTestId("comment-composer").textContent).toContain(
        "Plan"
      )
      fireEvent.change(screen.getByTestId("comment-input"), {
        target: { value: "Clarify this step" },
      })
      fireEvent.keyDown(screen.getByTestId("comment-input"), { key: "Enter" })
      expect(mocks.addPlanComment).not.toHaveBeenCalled()
      if (method === "click") {
        fireEvent.click(screen.getByRole("button", { name: "Comment" }))
      } else {
        fireEvent.keyDown(screen.getByTestId("comment-input"), {
          key: "Enter",
          [method]: true,
        })
      }

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
    }
  )

  it("adds a general comment without selecting text and clears a previous anchor", async () => {
    mocks.addPlanComment.mockResolvedValue({ ...comment, anchor: null })
    render(<PlanReview plan={plan} />)

    fireEvent.click(screen.getByRole("button", { name: "Add comment" }))
    expect(
      screen.getByTestId("comment-composer").querySelector("blockquote")
    ).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(screen.queryByTestId("comment-composer")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    fireEvent.change(screen.getByTestId("comment-input"), {
      target: { value: "Clarify this step" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add comment" }))
    expect(
      screen.getByTestId("comment-composer").querySelector("blockquote")
    ).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Comment" }))

    await waitFor(() =>
      expect(mocks.addPlanComment).toHaveBeenCalledWith(
        "thread-1",
        "Clarify this step",
        null
      )
    )
    const posted = await screen.findByTestId("plan-comment")
    expect(posted.textContent).toContain("Clarify this step")
    expect(posted.querySelector("blockquote")).toBeNull()
    expect(screen.queryByTestId("comment-composer")).toBeNull()
  })

  it("rolls back a failed optimistic comment without losing the draft", async () => {
    let rejectComment!: (error: Error) => void
    mocks.addPlanComment.mockReturnValueOnce(
      new Promise<PlanComment>((_, reject) => {
        rejectComment = reject
      })
    )
    render(<PlanReview plan={plan} />)
    fireEvent.click(screen.getByRole("button", { name: "Add comment" }))
    fireEvent.change(screen.getByTestId("comment-input"), {
      target: { value: "General feedback" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Comment" }))
    expect(screen.getByTestId("plan-comment").textContent).toContain(
      "General feedback"
    )
    expect(screen.queryByTestId("comment-delete")).toBeNull()

    const error = new Error("Offline")
    rejectComment(error)
    await waitFor(() => expect(screen.queryByTestId("plan-comment")).toBeNull())
    expect(
      (screen.getByTestId("comment-input") as HTMLTextAreaElement).value
    ).toBe("General feedback")
    expect(mocks.reportError).toHaveBeenCalledWith({
      title: "Couldn't add comment",
      error,
    })
  })

  it("submits comments once and allows another submission after new feedback", async () => {
    render(<PlanReview plan={plan} />)
    expect(screen.queryByRole("button", { name: "Submit comments" })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    fireEvent.change(screen.getByTestId("comment-input"), {
      target: { value: "Clarify this step" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Comment" }))
    fireEvent.click(
      await screen.findByRole("button", { name: "Submit comments" })
    )
    await waitFor(() =>
      expect(mocks.submitPlanComments).toHaveBeenCalledWith("thread-1")
    )
    expect(
      (
        screen.getByRole("button", {
          name: "Comments submitted",
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    mocks.addPlanComment.mockResolvedValueOnce({ ...comment, id: "comment-2" })
    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    fireEvent.change(screen.getByTestId("comment-input"), {
      target: { value: "One more thing" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Comment" }))
    expect(
      await screen.findByRole("button", { name: "Submit comments" })
    ).toBeTruthy()
  })

  it("shows historical artifacts without approval or implementation actions", async () => {
    mocks.getPlanComments.mockResolvedValue([comment])
    render(<PlanReview plan={{ ...plan, status: "approved" }} />)

    expect(screen.getByRole("heading", { name: "Artifact" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Request changes" })).toBeNull()
    expect((await screen.findByTestId("plan-comment")).textContent).toContain(
      "Clarify this step"
    )
    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    expect(screen.getByTestId("comment-input")).toBeTruthy()
  })

  it("loads and deletes comments on shared artifacts without decision actions", async () => {
    mocks.getPlanComments.mockResolvedValue([comment])
    render(<PlanReview plan={plan} />)

    expect((await screen.findByTestId("plan-comment")).textContent).toContain(
      "Clarify this step"
    )
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Request changes" })).toBeNull()
    fireEvent.click(screen.getByTestId("comment-delete"))
    await waitFor(() => expect(screen.queryByTestId("plan-comment")).toBeNull())
    expect(mocks.deletePlanComment).toHaveBeenCalledWith(
      "thread-1",
      "comment-1"
    )
  })
})
