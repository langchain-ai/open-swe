/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ReasoningBlock } from "./ReasoningBlock"

afterEach(() => cleanup())

describe("ReasoningBlock", () => {
  it("previews the thought while collapsed and shows all of it once expanded", () => {
    render(
      <ReasoningBlock
        isLive={false}
        text={"**Checking the snapshot**\n\nThe image looks stale."}
      />
    )

    expect(screen.getByText("Checking the snapshot")).toBeTruthy()
    expect(screen.queryByText(/The image looks stale/)).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Thought" }))

    expect(screen.queryByText("Checking the snapshot")).toBeNull()
    expect(screen.getByText(/The image looks stale/)).toBeTruthy()
  })
})
