import { describe, expect, it } from "vitest"

import {
  ghosttyMouseButton,
  isTerminalAltGraphText,
  isTerminalCompositionCommitInput,
  isTerminalCopyShortcut,
  isTerminalLinkPointerGesture,
  isTerminalPasteShortcut,
  shouldReportTerminalMouse,
  terminalLinkAtPosition,
  terminalWheelArrowData,
  terminalWheelDeltaRows,
} from "./surface"
import type { GhosttyCell, GhosttyRow } from "./core"

const cell = (text: string): GhosttyCell => ({
  text,
  wide: 0,
  foreground: { r: 255, g: 255, b: 255 },
  background: { r: 0, g: 0, b: 0 },
  bold: false,
  italic: false,
  invisible: false,
  strikethrough: false,
  overline: false,
  underline: false,
  selected: false,
})

describe("Ghostty terminal browser input", () => {
  it("preserves IME/AltGraph and platform clipboard shortcuts", () => {
    expect(
      isTerminalAltGraphText({
        key: "@",
        getModifierState: (modifier) => modifier === "AltGraph",
      })
    ).toBe(true)
    expect(
      isTerminalCompositionCommitInput({ inputType: "insertFromComposition" })
    ).toBe(true)
    const event = { ctrlKey: false, key: "c", metaKey: true, shiftKey: false }
    expect(isTerminalCopyShortcut(event, "MacIntel")).toBe(true)
    expect(isTerminalPasteShortcut({ ...event, key: "v" }, "MacIntel")).toBe(
      true
    )
    expect(
      isTerminalCopyShortcut(
        { ...event, ctrlKey: true, metaKey: false },
        "Linux"
      )
    ).toBe(false)
  })

  it("keeps mouse reporting, links, and alternate-screen wheel data", () => {
    expect(
      shouldReportTerminalMouse(true, {
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
      })
    ).toBe(true)
    expect([0, 1, 2, 3, 4, 5].map(ghosttyMouseButton)).toEqual([
      1,
      3,
      2,
      4,
      5,
      null,
    ])
    expect(
      isTerminalLinkPointerGesture({ ctrlKey: true, metaKey: false }, "Linux")
    ).toBe(true)
    expect(terminalWheelArrowData(-1, true)).toBe("\u001bOA")
    expect(
      terminalWheelDeltaRows({ deltaY: 8, deltaMode: 0 }, 16, 24, 0.5)
    ).toEqual({ rows: 1, remainder: 0 })
  })
})

describe("Ghostty terminal links", () => {
  it("reconstructs a soft-wrapped URL", () => {
    const row = (text: string, isWrapContinuation: boolean): GhosttyRow => ({
      cells: Array.from(text.padEnd(16), (character) => cell(character)),
      text: text.trimEnd(),
      isWrapContinuation,
      wrapsToNext: false,
    })
    const rows = [row("https://example.", false), row("com/reference", true)]
    expect(terminalLinkAtPosition(rows, 1, 4)).toBe(
      "https://example.com/reference"
    )
  })
})
