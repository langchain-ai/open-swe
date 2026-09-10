/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CopyButton } from "./copy-button"
import { TooltipProvider } from "./tooltip"

const writeText = vi.fn()

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  writeText.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function renderCopyButton() {
  render(
    <TooltipProvider>
      <CopyButton text={"  hello  \n\n"} label="Copy message" />
    </TooltipProvider>
  )
}

describe("CopyButton", () => {
  it("copies exact text and keeps feedback visible after a repeated copy", async () => {
    renderCopyButton()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy message" }))
    })
    expect(writeText).toHaveBeenCalledWith("  hello  \n\n")
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy()

    act(() => vi.advanceTimersByTime(1000))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copied" }))
    })
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy()

    act(() => vi.advanceTimersByTime(500))
    expect(screen.getByRole("button", { name: "Copy message" })).toBeTruthy()
  })

  it("does not report success when clipboard access is denied", async () => {
    writeText.mockRejectedValue(new Error("denied"))
    renderCopyButton()
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy message" }))
    })
    expect(writeText).toHaveBeenCalledOnce()
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull()
    expect(screen.getByRole("button", { name: "Copy message" })).toBeTruthy()
  })
})
