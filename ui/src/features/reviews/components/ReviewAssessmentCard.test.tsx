/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type {
  PublishedReviewAssessment,
  ReviewAssessmentFeedbackInput,
} from "@/lib/api"
import { ReviewAssessmentCard } from "./ReviewAssessmentCard"

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  save: vi.fn(),
  login: "alice",
}))
vi.mock("@/lib/api", () => ({
  api: { getAssessmentFeedback: mocks.get, saveAssessmentFeedback: mocks.save },
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: mocks.login } }),
}))

const assessment: PublishedReviewAssessment = {
  review_id: 123,
  head_sha: "a".repeat(40),
  risk_score: 1,
  decision: "would_approve",
  explanation: "Only documentation changed.",
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const card = (reviewId: number) => (
    <QueryClientProvider client={client}>
      <ReviewAssessmentCard
        assessment={{ ...assessment, review_id: reviewId }}
        owner="o"
        repo="r"
        number={1}
        headSha={assessment.head_sha}
      />
    </QueryClientProvider>
  )
  const result = render(card(123))
  return {
    client,
    rerender: (reviewId = 123) => result.rerender(card(reviewId)),
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.login = "alice"
  mocks.get.mockResolvedValue(null)
  mocks.save.mockImplementation(
    async (
      _owner: string,
      _repo: string,
      _number: number,
      _reviewId: number,
      feedback: ReviewAssessmentFeedbackInput
    ) => ({ ...feedback, login: mocks.login, updated_at: "2026-09-19" })
  )
})
afterEach(cleanup)

it("requires an explicit rating, saves the comment, collapses, and allows editing", async () => {
  renderCard()
  fireEvent.click(
    await screen.findByRole("button", { name: "Rate assessment" })
  )
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Clear explanation" },
  })
  expect(
    (screen.getByRole("button", { name: "Save feedback" }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
  fireEvent.click(screen.getByRole("button", { name: "Helpful" }))
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await screen.findByText("Feedback saved")
  expect(screen.queryByRole("textbox")).toBeNull()
  expect(mocks.save).toHaveBeenCalledExactlyOnceWith("o", "r", 1, 123, {
    rating: "helpful",
    comment: "Clear explanation",
  })
  fireEvent.click(screen.getByRole("button", { name: "Edit feedback" }))
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "Clear explanation"
  )
})

it("retains the draft after a failed save and supports retry", async () => {
  mocks.save.mockRejectedValueOnce(new Error("Offline"))
  renderCard()
  fireEvent.click(
    await screen.findByRole("button", { name: "Rate assessment" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }))
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Missed a migration" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await screen.findByRole("alert")
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "Missed a migration"
  )
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await screen.findByText("Feedback saved")
})

it("loads saved feedback without sharing drafts across assessments or users", async () => {
  mocks.get.mockResolvedValueOnce({
    rating: "unhelpful",
    comment: "Previous feedback",
    login: "alice",
    updated_at: "2026-09-19",
  })
  const { rerender } = renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "Edit feedback" }))
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "Previous feedback"
  )
  rerender(124)
  fireEvent.click(
    await screen.findByRole("button", { name: "Rate assessment" })
  )
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("")
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Private draft" },
  })
  mocks.login = "bob"
  rerender(124)
  fireEvent.click(
    await screen.findByRole("button", { name: "Rate assessment" })
  )
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("")
})

it("does not overwrite feedback when loading it fails", async () => {
  mocks.get.mockRejectedValueOnce(new Error("Offline"))
  renderCard()
  await screen.findByRole("alert")
  expect(screen.queryByRole("button", { name: "Rate assessment" })).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))
  await screen.findByRole("button", { name: "Rate assessment" })
})

it("keeps the saved feedback when an older refetch finishes afterward", async () => {
  const { client } = renderCard()
  fireEvent.click(
    await screen.findByRole("button", { name: "Rate assessment" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Helpful" }))
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "New comment" },
  })
  let resolveOld!: (value: null) => void
  mocks.get.mockImplementationOnce(
    () =>
      new Promise<null>((resolve) => {
        resolveOld = resolve
      })
  )
  const pending = client.refetchQueries({ queryKey: ["assessment-feedback"] })
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await screen.findByText("Feedback saved")
  await act(async () => {
    resolveOld(null)
    await pending
  })
  fireEvent.click(screen.getByRole("button", { name: "Edit feedback" }))
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "New comment"
  )
})
