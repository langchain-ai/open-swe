/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ContextWindowMeter } from "./ContextWindowMeter"

afterEach(cleanup)

describe("ContextWindowMeter", () => {
  it("stays hidden until usage is reported", () => {
    const { container } = render(<ContextWindowMeter contextWindow={200_000} />)
    expect(container.firstChild).toBeNull()
  })

  it("opens details with the model, context limit, and thread cost", async () => {
    render(
      <ContextWindowMeter
        usedTokens={180_000}
        contextWindow={200_000}
        model="Opus"
        costUsd={0.005}
      />,
      { wrapper: TooltipProvider }
    )
    fireEvent.click(screen.getByRole("button", { name: /90% used/ }))
    const details = within(
      await screen.findByRole("dialog", { name: "Context window" })
    )
    expect(details.getByText("Context nearly full")).toBeTruthy()
    expect(details.getByText("Opus")).toBeTruthy()
    expect(details.getByText("Thread cost")).toBeTruthy()
    expect(details.getByText("<$0.01")).toBeTruthy()
  })

  it("shows consumption even without a reported context limit", async () => {
    render(<ContextWindowMeter usedTokens={84_000} />, {
      wrapper: TooltipProvider,
    })
    fireEvent.click(
      screen.getByRole("button", { name: /84,000 tokens used, limit unknown/ })
    )
    const details = within(
      await screen.findByRole("dialog", { name: "Context window" })
    )
    expect(
      details.getByText(/provider has not reported a context limit/)
    ).toBeTruthy()
    expect(details.queryByRole("progressbar")).toBeNull()
  })
})
