/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { RunErrorBanner } from "./RunErrorBanner"

const wrapper = TooltipProvider
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it("prevents repeated retries while a request is pending and recovers after a failure", async () => {
  let rejectRequest: (error: Error) => void = () => {}
  const request = new Promise<void>((_resolve, reject) => {
    rejectRequest = reject
  })
  const retry = vi.fn(() => request)
  render(<RunErrorBanner threadId="thread-1" retry={retry} />, { wrapper })

  const button = screen.getByRole("button", { name: "Retry" })
  fireEvent.click(button)
  fireEvent.click(button)
  expect(retry).toHaveBeenCalledOnce()
  expect(button.hasAttribute("disabled")).toBe(true)

  await act(async () => rejectRequest(new Error("Couldn't send message")))

  expect(button.hasAttribute("disabled")).toBe(false)
  expect(screen.getByText("The run failed.")).toBeTruthy()
})

it("keeps diagnostics available without offering a retry to read-only viewers", () => {
  render(
    <RunErrorBanner
      threadId="thread-1"
      traceUrl="https://smith.langchain.com/trace/example"
    />,
    { wrapper }
  )

  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  expect(
    screen.getByRole("button", { name: "Copy debugging info" })
  ).toBeTruthy()
  expect(
    screen.getByRole("link", { name: "Open trace" }).getAttribute("href")
  ).toBe("https://smith.langchain.com/trace/example")
})

it("does not retry while the thread is busy or still loading", () => {
  const retry = vi.fn(async () => {})
  render(<RunErrorBanner threadId="thread-1" retry={retry} disabled />, {
    wrapper,
  })

  fireEvent.click(screen.getByRole("button", { name: "Retry" }))

  expect(retry).not.toHaveBeenCalled()
})
