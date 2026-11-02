/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReasoningBlock } from "./ReasoningBlock"

afterEach(() => cleanup())

describe("ReasoningBlock", () => {
  it("previews the reasoning on its toggle and shows all of it once expanded", () => {
    render(
      <ReasoningBlock
        isLive={false}
        text={"**Checking the snapshot**\n\nThe image looks stale."}
      />
    )

    const toggle = screen.getByRole("button", { name: "Checking the snapshot" })
    expect(screen.queryByText(/The image looks stale/)).toBeNull()

    fireEvent.click(toggle)

    expect(screen.getByText(/The image looks stale/)).toBeTruthy()
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
