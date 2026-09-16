/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { DesktopThreadFeedbackDialog } from "./DesktopThreadFeedbackDialog"

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

it("submits positive desktop feedback", async () => {
  render(
    <DesktopThreadFeedbackDialog
      open
      onOpenChange={vi.fn()}
      threadId="local-1"
    />
  )

  fireEvent.click(screen.getByRole("button", { name: "Good" }))
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))

  await screen.findByText("Thanks for your feedback.")
  expect(submitThreadFeedback).toHaveBeenCalledExactlyOnceWith({
    threadId: "local-1",
    rating: "good",
    comment: "",
  })
})

it("includes an optional comment with negative feedback", async () => {
  render(
    <DesktopThreadFeedbackDialog
      open
      onOpenChange={vi.fn()}
      threadId="local-1"
    />
  )

  fireEvent.click(screen.getByRole("button", { name: "Bad" }))
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: " More detail please. " },
  })
  fireEvent.click(screen.getByRole("button", { name: "Submit feedback" }))

  await screen.findByText("Thanks for your feedback.")
  expect(submitThreadFeedback).toHaveBeenCalledExactlyOnceWith({
    threadId: "local-1",
    rating: "bad",
    comment: "More detail please.",
  })
})
