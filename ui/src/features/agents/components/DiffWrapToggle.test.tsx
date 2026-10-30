/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { DiffWrapToggle } from "./DiffWrapToggle"

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

describe("DiffWrapToggle", () => {
  it("toggles and persists line wrapping", () => {
    render(<DiffWrapToggle />, { wrapper: TooltipProvider })
    const toggle = screen.getByRole("button", { name: "Wrap lines" })

    expect(toggle.getAttribute("aria-pressed")).toBe("false")

    fireEvent.click(toggle)

    expect(toggle.getAttribute("aria-pressed")).toBe("true")
    expect(window.localStorage.getItem("open-swe.diff.overflow")).toBe("wrap")
  })

  it("synchronizes mounted controls", () => {
    render(
      <>
        <DiffWrapToggle />
        <DiffWrapToggle />
      </>,
      { wrapper: TooltipProvider }
    )
    const toggles = screen.getAllByRole("button", { name: "Wrap lines" })

    fireEvent.click(toggles[0] as HTMLButtonElement)

    expect(
      toggles.map((toggle) => toggle.getAttribute("aria-pressed"))
    ).toEqual(["true", "true"])
  })
})
