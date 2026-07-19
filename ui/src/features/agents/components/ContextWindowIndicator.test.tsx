/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ContextWindowIndicator } from "./ContextWindowIndicator"

afterEach(() => cleanup())

describe("ContextWindowIndicator", () => {
  it("renders the context window before usage is available", () => {
    render(<ContextWindowIndicator contextWindow={200_000} />)

    const indicator = screen.getByTestId("context-window-indicator")
    expect(indicator.textContent).toContain("200.0K context")
    expect(indicator.getAttribute("title")).toBe(
      "Model context window: 200.0K tokens"
    )
  })

  it("renders usage percentage when usage and limit are available", () => {
    render(
      <ContextWindowIndicator usedTokens={84_000} contextWindow={200_000} />
    )

    const indicator = screen.getByTestId("context-window-indicator")
    expect(indicator.textContent).toContain("42%")
    expect(indicator.getAttribute("title")).toBe(
      "Context: 84.0K / 200.0K tokens (42%)"
    )
  })

  it("renders usage without a known limit", () => {
    render(<ContextWindowIndicator usedTokens={84_000} />)

    const indicator = screen.getByTestId("context-window-indicator")
    expect(indicator.textContent).toContain("84.0K tokens")
    expect(indicator.getAttribute("title")).toBe(
      "84.0K tokens used. Context window unavailable for this model."
    )
  })

  it("warns near the context limit", () => {
    render(
      <ContextWindowIndicator usedTokens={180_000} contextWindow={200_000} />
    )

    const indicator = screen.getByTestId("context-window-indicator")
    expect(indicator.textContent).toContain("90%")
    expect(indicator.getAttribute("title")).toContain(
      "Approaching context limit."
    )
    expect(indicator.className).toContain("--ui-danger")
  })
})
