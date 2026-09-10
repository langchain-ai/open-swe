/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ThreadFeedbackCard } from "./ThreadFeedbackCard"

const api = vi.hoisted(() => ({
  getThreadFeedback: vi.fn(),
  submitThreadFeedback: vi.fn(),
}))
vi.mock("@/features/agents/lib/api", () => ({ agentsApi: api }))

const ready = { status: "ready", rating: null, comment: "" }

function renderCard() {
  const client = new QueryClient()
  const card = (active: boolean) => (
    <QueryClientProvider client={client}>
      {!active && <ThreadFeedbackCard threadId="thread-1" login="owner" />}
    </QueryClientProvider>
  )
  const result = render(card(false))
  return (active: boolean) => result.rerender(card(active))
}

beforeEach(() => {
  api.getThreadFeedback.mockResolvedValue(ready)
  api.submitThreadFeedback.mockImplementation(async (_threadId, body) => ({
    ...ready,
    ...body,
    status: body.action === "dismiss" ? "dismissed" : "completed",
  }))
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it.each([
  { rating: "good", label: "🙂 Good", comment: "" },
  { rating: "bad", label: "💩 Bad", comment: "The tests still fail." },
])(
  "saves $rating and an optional comment only on Submit",
  async ({ rating, label, comment }) => {
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
    fireEvent.click(screen.getByRole("button", { name: label }))
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: comment },
    })
    expect(api.submitThreadFeedback).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
    await screen.findByText("Thanks for your feedback.")
    expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith(
      "thread-1",
      { rating, comment }
    )
    expect(screen.queryByRole("button")).toBeNull()
  }
)

it("requires a comment for Other and keeps the draft when saving fails", async () => {
  api.submitThreadFeedback.mockRejectedValueOnce(new Error("Temporary outage"))
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💬 Other" }))
  const comment = screen.getByRole("textbox", { name: "Comment (required)" })
  const submit = screen.getByRole("button", {
    name: "Submit feedback",
  }) as HTMLButtonElement
  fireEvent.change(comment, { target: { value: "  " } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(comment, { target: { value: " More detail please. " } })
  fireEvent.click(submit)
  await screen.findByRole("alert")
  expect((comment as HTMLTextAreaElement).value).toBe(" More detail please. ")
  fireEvent.click(submit)
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenLastCalledWith("thread-1", {
    rating: "other",
    comment: "More detail please.",
  })
})

it("dismisses the card without saving a rating", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }))
  await waitFor(() => expect(screen.queryByRole("form")).toBeNull())
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    action: "dismiss",
  })
})

it("hides during activity and rechecks eligibility before showing a cached prompt", async () => {
  const setActive = renderCard()
  await screen.findByRole("form")
  setActive(true)
  expect(screen.queryByRole("form")).toBeNull()
  let resolve!: (value: typeof ready) => void
  api.getThreadFeedback.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      })
  )
  setActive(false)
  expect(screen.queryByRole("form")).toBeNull()
  await act(async () => resolve({ ...ready, status: "unavailable" }))
  expect(screen.queryByRole("form")).toBeNull()
})
