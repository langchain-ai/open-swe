/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ContextWindowMeter } from "./ContextWindowMeter"

afterEach(() => cleanup())

/** The numbers live in a popover, so the collapsed ring carries the accessible label. */
function meterLabel() {
  return screen
    .getByTestId("context-window-indicator")
    .getAttribute("aria-label")
}

describe("ContextWindowMeter", () => {
  it("renders nothing without usage or a limit", () => {
    const { container } = render(<ContextWindowMeter />)
    expect(container.firstChild).toBeNull()
  })

  it("reports the context window before usage is available", () => {
    render(<ContextWindowMeter contextWindow={200_000} />)
    expect(meterLabel()).toBe("Context window 200.0K tokens")
  })

  it("reports a percentage once usage and limit are both known", () => {
    render(<ContextWindowMeter usedTokens={84_000} contextWindow={200_000} />)
    expect(meterLabel()).toBe("Context window 42% used")
  })

  it("reports usage without a known limit", () => {
    render(<ContextWindowMeter usedTokens={84_000} />)
    expect(meterLabel()).toBe("Context window 84.0K tokens")
  })

  it("warns on hover once the context limit is in reach", async () => {
    render(<ContextWindowMeter usedTokens={180_000} contextWindow={200_000} />)
    expect(meterLabel()).toBe("Context window 90% used")

    fireEvent.click(screen.getByTestId("context-window-indicator"))

    await waitFor(() =>
      expect(screen.getByText(/Approaching the context limit/)).toBeTruthy()
    )
    expect(screen.getByText("180.0K/200.0K")).toBeTruthy()
  })
})
