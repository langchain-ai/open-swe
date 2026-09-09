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
  dismissThreadFeedback: vi.fn(),
}))

vi.mock("@/features/agents/lib/api", () => ({ agentsApi: api }))

const ready = { status: "ready", promptAt: 1, rating: null, comment: "" }

function renderCard(isActive = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const card = (active: boolean) => (
    <QueryClientProvider client={client}>
      <ThreadFeedbackCard threadId="thread-1" login="owner" isActive={active} />
    </QueryClientProvider>
  )
  const result = render(card(isActive))
  return {
    ...result,
    setActive: (active: boolean) => result.rerender(card(active)),
  }
}

beforeEach(() => {
  api.getThreadFeedback.mockResolvedValue(ready)
  api.submitThreadFeedback.mockImplementation(async (_threadId, body) => ({
    ...ready,
    ...body,
    status: "completed",
  }))
  api.dismissThreadFeedback.mockResolvedValue({ ...ready, status: "dismissed" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it("keeps rating changes local until Submit, then removes the controls", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
  fireEvent.click(screen.getByRole("button", { name: "🙂 Good" }))
  expect(api.submitThreadFeedback).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "good",
    comment: "",
  })
  expect(screen.queryByRole("button")).toBeNull()
})

it("reveals and requires a comment for Other", async () => {
  renderCard()
  expect(screen.queryByRole("textbox")).toBeNull()
  fireEvent.click(await screen.findByRole("button", { name: "💬 Other" }))
  const submit = screen.getByRole("button", {
    name: "Submit feedback",
  }) as HTMLButtonElement
  const comment = screen.getByRole("textbox", { name: "Comment (required)" })
  fireEvent.change(comment, { target: { value: "  " } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(comment, { target: { value: " I needed more detail. " } })
  fireEvent.click(submit)
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "other",
    comment: "I needed more detail.",
  })
})

it("allows an optional comment with a rating", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💩 Bad" }))
  fireEvent.click(screen.getByRole("button", { name: "Add a comment" }))
  fireEvent.change(
    screen.getByRole("textbox", { name: "Comment (optional)" }),
    {
      target: { value: "The tests still fail." },
    }
  )
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
  await screen.findByText("Thanks for your feedback.")
  expect(api.submitThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1", {
    rating: "bad",
    comment: "The tests still fail.",
  })
})

it("hides dismissed feedback without saving a rating", async () => {
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }))
  await waitFor(() =>
    expect(screen.queryByText("How did Open SWE do?")).toBeNull()
  )
  expect(api.submitThreadFeedback).not.toHaveBeenCalled()
  expect(api.dismissThreadFeedback).toHaveBeenCalledExactlyOnceWith("thread-1")
})

it("preserves the draft after a failed save and allows retry", async () => {
  api.submitThreadFeedback.mockRejectedValueOnce(new Error("Temporary outage"))
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "💬 Other" }))
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "More context please." },
  })
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
  await screen.findByRole("alert")
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "More context please."
  )
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
  await screen.findByText("Thanks for your feedback.")
})

it("hides feedback while the agent is active", () => {
  renderCard(true)
  expect(screen.queryByText("How did Open SWE do?")).toBeNull()
  expect(api.getThreadFeedback).not.toHaveBeenCalled()
})

it("rechecks feedback after new activity before showing a cached prompt", async () => {
  const rendered = renderCard()
  await screen.findByRole("button", { name: "🙂 Good" })
  rendered.setActive(true)
  expect(screen.queryByRole("button", { name: "🙂 Good" })).toBeNull()
  let resolve: (value: typeof ready) => void = () => {}
  api.getThreadFeedback.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      })
  )
  rendered.setActive(false)
  expect(screen.queryByRole("button", { name: "🙂 Good" })).toBeNull()
  await act(async () => resolve({ ...ready, status: "waiting" }))
  expect(screen.queryByRole("button", { name: "🙂 Good" })).toBeNull()
})

it("disables submission and dismissal while saving", async () => {
  let resolve: (value: typeof ready) => void = () => {}
  api.submitThreadFeedback.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      })
  )
  renderCard()
  fireEvent.click(await screen.findByRole("button", { name: "🙂 Good" }))
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))
  await screen.findByRole("button", { name: "Saving…" })
  expect(
    (screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
  expect(
    (screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement)
      .disabled
  ).toBe(true)
  await act(async () => resolve({ ...ready, status: "completed" }))
  await screen.findByText("Thanks for your feedback.")
})

it.each(["waiting", "unavailable", "dismissed"])(
  "hides %s feedback",
  async (status) => {
    api.getThreadFeedback.mockResolvedValue({ ...ready, status })
    renderCard()
    await waitFor(() => expect(api.getThreadFeedback).toHaveBeenCalled())
    expect(screen.queryByText("How did Open SWE do?")).toBeNull()
  }
)
