/** @vitest-environment jsdom */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ConversationFind } from "./ConversationFind"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

const highlights = new Map<string, Set<Range>>()
vi.stubGlobal("CSS", { highlights })
vi.stubGlobal(
  "Highlight",
  class extends Set<Range> {
    constructor(...ranges: Range[]) {
      super(ranges)
    }
  }
)
Object.defineProperty(Element.prototype, "checkVisibility", {
  configurable: true,
  value: function (this: Element) {
    return !this.closest('[style="display: none;"]')
  },
})
Object.defineProperty(Range.prototype, "getBoundingClientRect", {
  configurable: true,
  value: () => ({ top: 100 }),
})

afterEach(() => {
  cleanup()
  highlights.clear()
})

describe("ConversationFind", () => {
  it("finds rendered phrases across formatting, skips hidden details, wraps and updates without jumping on streamed text", async () => {
    const root = document.createElement("div")
    root.innerHTML =
      '<p>Version <strong>one</strong>, version one.</p><div style="display: none;">version one</div><button>version one</button>'
    document.body.append(root)
    const previous = document.createElement("textarea")
    document.body.append(previous)
    previous.focus()
    render(
      <TooltipProvider>
        <ConversationFind root={{ current: root }} hasEarlier />
      </TooltipProvider>
    )
    fireEvent.keyDown(previous, { key: "f", ctrlKey: true })
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Find in conversation" })
      )
    )
    const input = screen.getByRole("textbox", { name: "Find in conversation" })
    fireEvent.change(input, { target: { value: "VERSION ONE" } })
    await waitFor(() => expect(screen.getByText("1 of 2")).toBeTruthy())
    expect(
      [...highlights.get("conversation-find")!].map((range) => range.toString())
    ).toEqual(["Version one", "version one"])
    expect(screen.getByText("Searching loaded messages only")).toBeTruthy()
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true })
    expect(screen.getByText("2 of 2")).toBeTruthy()
    fireEvent.keyDown(input, { key: "Enter" })
    expect(screen.getByText("1 of 2")).toBeTruthy()
    await act(async () => {
      root.insertAdjacentHTML("beforeend", "<p>version one</p>")
    })
    expect(screen.getByText("1 of 3")).toBeTruthy()
    fireEvent.change(input, { target: { value: "absent" } })
    await waitFor(() => expect(screen.getByText("No results")).toBeTruthy())
    expect(
      screen
        .getByRole("button", { name: "Next match" })
        .hasAttribute("disabled")
    ).toBe(true)
    fireEvent.keyDown(input, { key: "Escape" })
    expect(screen.queryByRole("search")).toBeNull()
    expect(highlights.size).toBe(0)
    expect(document.activeElement).toBe(previous)
    root.remove()
    previous.remove()
  })
})
