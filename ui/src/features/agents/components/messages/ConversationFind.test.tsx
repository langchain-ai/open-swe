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

const commands = vi.hoisted(() => ({
  registered: [] as ReadonlyArray<import("@/lib/appCommands").AppCommand>,
  review: false,
}))
vi.mock("@/lib/appCommands", () => ({
  useAppCommand: () => (commands.review ? { id: "review-find" } : undefined),
  useRegisterAppCommands: (registered: typeof commands.registered) => {
    commands.registered = registered
  },
}))

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
  value: () => ({ top: 100, bottom: 120 }),
})

afterEach(() => {
  cleanup()
  highlights.clear()
})

describe("ConversationFind", () => {
  it("keeps block boundaries and Unicode offsets faithful to the rendered text", async () => {
    const root = document.createElement("div")
    root.innerHTML =
      '<h2>Summary</h2><h2>Next steps</h2><div>foo<p>bar</p>baz</div><p>İa [literal]</p><div style="overflow-y: hidden;">clipped</div>'
    document.body.append(root)
    render(
      <TooltipProvider>
        <ConversationFind root={{ current: root }} hasEarlier={false} />
      </TooltipProvider>
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Find in conversation (⌘F / Ctrl+F)" })
    )
    const input = screen.getByRole("textbox", { name: "Find in conversation" })
    for (const value of ["ob", "clipped"]) {
      fireEvent.change(input, { target: { value } })
      await waitFor(() => expect(screen.getByText("No results")).toBeTruthy())
    }
    fireEvent.change(input, { target: { value: "ynext" } })
    await waitFor(() => expect(screen.getByText("1 of 1")).toBeTruthy())
    expect(
      [...highlights.get("conversation-find")!].map((range) => range.toString())
    ).toEqual(["Next"])
    fireEvent.change(input, { target: { value: "a [literal]" } })
    await waitFor(() => expect(screen.getByText("1 of 1")).toBeTruthy())
    expect(
      [...highlights.get("conversation-find")!].map((range) => range.toString())
    ).toEqual(["a [literal]"])
    root.remove()
  })

  it("finds rendered phrases across formatting, skips hidden details, wraps and updates without jumping on streamed text", async () => {
    const root = document.createElement("div")
    root.innerHTML =
      '<p>Version <strong>one</strong>, version one.</p><div style="display: none;">version one</div><button>version one</button>'
    document.body.append(root)
    const previous = document.createElement("textarea")
    document.body.append(previous)
    previous.focus()
    const { rerender } = render(
      <TooltipProvider>
        <ConversationFind root={{ current: root }} hasEarlier />
      </TooltipProvider>
    )
    expect(commands.registered[0]?.shortcuts).toEqual(["mod+f"])
    act(() => {
      void commands.registered[0]?.run?.()
    })
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
    await waitFor(() => expect(screen.getByText("1 of 3")).toBeTruthy())
    await act(async () => {
      root.insertAdjacentHTML("afterbegin", "<p>version one</p>")
    })
    await waitFor(() => expect(screen.getByText("2 of 4")).toBeTruthy())
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
    await waitFor(() => expect(document.activeElement).toBe(previous))
    const button = screen.getByRole("button", {
      name: "Find in conversation (⌘F / Ctrl+F)",
    })
    button.focus()
    fireEvent.click(button)
    fireEvent.click(screen.getByRole("button", { name: "Close find" }))
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", {
          name: "Find in conversation (⌘F / Ctrl+F)",
        })
      )
    )
    commands.review = true
    rerender(
      <TooltipProvider>
        <ConversationFind root={{ current: root }} hasEarlier />
      </TooltipProvider>
    )
    expect(commands.registered[0]?.shortcuts).toEqual([])
    commands.review = false
    root.remove()
    previous.remove()
  })
})
