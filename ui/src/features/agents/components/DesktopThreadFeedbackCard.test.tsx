/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { DesktopThreadFeedbackCard } from "./DesktopThreadFeedbackCard"

const submitThreadFeedback = vi.fn()

beforeEach(() => {
  vi.resetAllMocks()
  submitThreadFeedback.mockResolvedValue({ exported: true })
  window.openSweDesktop = {
    submitThreadFeedback,
  } as unknown as Window["openSweDesktop"]
})

afterEach(() => {
  cleanup()
  delete window.openSweDesktop
})

it("submits positive desktop feedback inline", async () => {
  render(<DesktopThreadFeedbackCard threadId="local-1" />)

  fireEvent.click(screen.getByRole("button", { name: "Good" }))

  await screen.findByText("Thanks for your feedback.")
  expect(submitThreadFeedback).toHaveBeenCalledExactlyOnceWith({
    threadId: "local-1",
    rating: "good",
    comment: "",
  })
})

it("asks for an optional comment after negative feedback", async () => {
  render(<DesktopThreadFeedbackCard threadId="local-1" />)

  fireEvent.click(screen.getByRole("button", { name: "Bad" }))
  await screen.findByText("How could Open SWE do better?")
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: " More detail please. " },
  })
  fireEvent.click(screen.getByRole("button", { name: "Submit comment" }))

  await screen.findByText("Thanks for your feedback.")
  expect(submitThreadFeedback).toHaveBeenNthCalledWith(1, {
    threadId: "local-1",
    rating: "bad",
    comment: "",
  })
  expect(submitThreadFeedback).toHaveBeenNthCalledWith(2, {
    threadId: "local-1",
    rating: "bad",
    comment: "More detail please.",
  })
})

it("dismisses without saving feedback", () => {
  render(<DesktopThreadFeedbackCard threadId="local-1" />)

  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }))

  expect(screen.queryByLabelText("Thread feedback")).toBeNull()
  expect(submitThreadFeedback).not.toHaveBeenCalled()
})
