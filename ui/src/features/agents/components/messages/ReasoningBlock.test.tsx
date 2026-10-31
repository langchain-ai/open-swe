/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReasoningBlock } from "./ReasoningBlock"

afterEach(() => cleanup())

describe("ReasoningBlock", () => {
  it("previews the reasoning on its toggle and renders the rest as markdown once expanded", () => {
    const { container } = render(
      <ReasoningBlock
        isLive={false}
        text={"**Checking the snapshot**\n\nThe image looks **stale**."}
      />
    )

    const toggle = screen.getByRole("button", { name: "Checking the snapshot" })
    expect(screen.queryByText(/The image looks/)).toBeNull()

    fireEvent.click(toggle)

    expect(screen.getByText(/The image looks/)).toBeTruthy()
    expect(container.querySelector("strong")?.textContent).toBe("stale")
    expect(container.textContent).not.toContain("**")
    expect(screen.getAllByText(/Checking the snapshot/)).toHaveLength(1)
  })

  it("names the live thought by its heading", () => {
    render(
      <ReasoningBlock
        isLive
        text={"**Checking the snapshot**\n\nThe image looks"}
      />
    )

    expect(
      screen.getByRole("button", { name: "Checking the snapshot..." })
    ).toBeTruthy()
  })

  it("keeps underscores that are part of a name", () => {
    render(
      <ReasoningBlock
        isLive={false}
        text="Rename `reasoning_label` to _thought_preview_"
      />
    )

    expect(
      screen.getByRole("button", {
        name: "Rename reasoning_label to thought_preview",
      })
    ).toBeTruthy()
  })
})
