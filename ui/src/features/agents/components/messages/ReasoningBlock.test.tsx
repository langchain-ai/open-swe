/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReasoningBlock } from "./ReasoningBlock"

afterEach(() => cleanup())

describe("ReasoningBlock", () => {
  it("streams open under a shimmering header while live", () => {
    render(<ReasoningBlock text="Consider the options" isLive />)

    expect(screen.getByText("Thinking...").className).toContain("shimmer-text")
    expect(screen.getByText("Consider the options").textContent).toBe(
      "Consider the options"
    )
  })

  it("collapses into a toggle once the reasoning has settled", () => {
    render(<ReasoningBlock text="Consider the options" isLive={false} />)

    const trigger = screen.getByRole("button", { name: "Thought" })
    expect(trigger.getAttribute("aria-expanded")).toBe("false")
    expect(screen.queryByText("Consider the options")).toBeNull()

    fireEvent.click(trigger)

    expect(screen.getByText("Consider the options").textContent).toBe(
      "Consider the options"
    )
  })

  it("renders nothing for settled, empty reasoning", () => {
    const { container } = render(<ReasoningBlock text="  " isLive={false} />)

    expect(container.innerHTML).toBe("")
  })
})
