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
  vi.resetAllMocks()
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

it("shows only Good/Bad and saves Good without a comment form", async () => {
  renderCard()
  const good = await screen.findByRole("button", { name: "🙂 Good" })
  expect(screen.queryByRole("textbox")).toBeNull()
  expect(screen.queryByRole("button", { name: /Other|Submit/ })).toBeNull()
  fireEvent.click(good)
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "good",
  })
  expect(screen.queryByRole("button")).toBeNull()
})

it("saves Bad before offering an optional comment and keeps the draft on failure", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
  const comment = await screen.findByRole("textbox", {
    name: "Comment (optional)",
  })
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "bad",
  })
  expect(screen.queryByRole("button", { name: "🙂 Good" })).toBeNull()
  fireEvent.change(comment, { target: { value: " More detail please. " } })
  api.submitThreadFeedback.mockRejectedValueOnce(new Error("Temporary outage"))
  fireEvent.click(screen.getByRole("button", { name: "Submit comment" }))
  await screen.findByRole("alert")
  expect((comment as HTMLTextAreaElement).value).toBe(" More detail please. ")
  fireEvent.click(screen.getByRole("button", { name: "Submit comment" }))
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenLastCalledWith("thread-1", {
    action: "comment",
    comment: "More detail please.",
  })
  expect(screen.queryByRole("textbox")).toBeNull()
})

it("allows skipping the comment without undoing the Bad rating", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
  fireEvent.click(await screen.findByRole("button", { name: "Skip" }))
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "bad",
  })
  expect(screen.queryByRole("textbox")).toBeNull()
})

it("keeps the rating controls available when saving the rating fails", async () => {
  api.submitThreadFeedback.mockRejectedValueOnce(new Error("Temporary outage"))
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
  await screen.findByRole("alert")
  expect(screen.queryByRole("textbox")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "💩 Bad" }))
  await screen.findByRole("textbox")
})

it("does not reopen the comment form for previously completed feedback", async () => {
  api.getThreadFeedback.mockResolvedValue({
    status: "completed",
    rating: "bad",
    comment: "",
  })
  renderCard()
  await screen.findByText("Thanks for your feedback.")
  expect(screen.queryByRole("textbox")).toBeNull()
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
