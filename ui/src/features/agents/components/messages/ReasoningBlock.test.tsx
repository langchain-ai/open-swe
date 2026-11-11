/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
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
      />,
      { wrapper: TooltipProvider }
    )

    const toggle = screen.getByRole("button", {
      name: "Thought Checking the snapshot",
    })
    expect(screen.queryByText(/The image looks/)).toBeNull()

    fireEvent.click(toggle)

    expect(screen.getByText(/The image looks/)).toBeTruthy()
    expect(container.querySelector("strong")?.textContent).toBe("stale")
    expect(container.textContent).not.toContain("**")
    expect(screen.getAllByText(/Checking the snapshot/)).toHaveLength(1)
  })

  it("shows the live thought before a heading or newline arrives", () => {
    const { rerender } = render(<ReasoningBlock isLive text="" />)
    expect(screen.getByRole("button", { name: "Thinking..." })).toBeTruthy()

    rerender(<ReasoningBlock isLive text="Checking the snapshot" />)
    expect(
      screen.getByRole("button", { name: "Checking the snapshot..." })
    ).toBeTruthy()
    expect(screen.getAllByText(/Checking the snapshot/)).toHaveLength(1)

    rerender(
      <ReasoningBlock isLive text={"Checking the snapshot\nIt looks stale"} />
    )
    expect(screen.getAllByText(/Checking the snapshot/)).toHaveLength(1)
    expect(screen.getByText("It looks stale")).toBeTruthy()

    rerender(<ReasoningBlock isLive text="**Checking the snapshot**" />)
    expect(
      screen.getByRole("button", { name: "Checking the snapshot..." })
    ).toBeTruthy()
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

  it("keeps an earlier heading in the live body once the header moves on", () => {
    render(
      <ReasoningBlock
        isLive
        text={
          "**Checking the snapshot**\n\nIt looks stale.\n\n**Comparing captures**\n\nThe"
        }
      />
    )

    expect(
      screen.getByRole("button", { name: "Comparing captures..." })
    ).toBeTruthy()
    expect(screen.getByText("Checking the snapshot")).toBeTruthy()
  })

  it("keeps underscores that are part of a name", () => {
    render(
      <ReasoningBlock
        isLive={false}
        text="Rename `reasoning_label` to _thought_preview_"
      />,
      { wrapper: TooltipProvider }
    )

    expect(
      screen.getByRole("button", {
        name: "Thought Rename reasoning_label to thought_preview",
      })
    ).toBeTruthy()
  })
})
