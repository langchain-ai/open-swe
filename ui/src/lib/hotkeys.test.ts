/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import {
  createRapidKeyRun,
  eventMatchesShortcut,
  formatShortcut,
  isHotkeySuppressed,
  isTypingContext,
  shouldIgnoreHotkey,
} from "./hotkeys"

function eventWithTarget(
  target: Element,
  init: KeyboardEventInit = {}
): KeyboardEvent {
  const event = new KeyboardEvent("keydown", {
    bubbles: true,
    cancelable: true,
    key: "k",
    ...init,
  })
  Object.defineProperty(event, "target", { value: target })
  return event
}

describe("keyboard shortcut utilities", () => {
  it("resolves mod to the platform modifier", () => {
    expect(
      eventMatchesShortcut(
        new KeyboardEvent("keydown", { key: "k", metaKey: true }),
        "mod+k",
        "mac"
      )
    ).toBe(true)
    expect(
      eventMatchesShortcut(
        new KeyboardEvent("keydown", { key: "k", ctrlKey: true }),
        "mod+k",
        "other"
      )
    ).toBe(true)
    expect(
      eventMatchesShortcut(
        new KeyboardEvent("keydown", { key: "k", ctrlKey: true }),
        "mod+k",
        "mac"
      )
    ).toBe(false)
    expect(
      eventMatchesShortcut(
        new KeyboardEvent("keydown", { key: "?", shiftKey: true }),
        "?",
        "other"
      )
    ).toBe(true)
  })

  it("formats platform-specific shortcut labels", () => {
    expect(formatShortcut("mod+k", "mac")).toBe("⌘K")
    expect(formatShortcut("mod+alt+b", "other")).toBe("Ctrl Alt B")
    expect(formatShortcut("ctrl+`", "mac")).toBe("⌃`")
    expect(formatShortcut("shift+?", "other")).toBe("?")
  })

  it("detects every supported typing context including descendants", () => {
    const input = document.createElement("input")
    const textarea = document.createElement("textarea")
    const select = document.createElement("select")
    const textbox = document.createElement("div")
    textbox.setAttribute("role", "textbox")
    const editable = document.createElement("div")
    editable.setAttribute("contenteditable", "true")
    const child = document.createElement("span")
    editable.append(child)

    expect(isTypingContext(input)).toBe(true)
    expect(isTypingContext(textarea)).toBe(true)
    expect(isTypingContext(select)).toBe(true)
    expect(isTypingContext(document.createElement("iframe"))).toBe(true)
    expect(isTypingContext(textbox)).toBe(true)
    expect(isTypingContext(child)).toBe(true)
    expect(isTypingContext(document.createElement("button"))).toBe(false)
  })

  it("suppresses app shortcuts while typing or inside an ignored region", () => {
    const input = document.createElement("input")
    const ignored = document.createElement("div")
    ignored.dataset.hotkeys = "ignore"
    const child = document.createElement("button")
    ignored.append(child)

    expect(shouldIgnoreHotkey(eventWithTarget(input))).toBe(true)
    expect(isHotkeySuppressed(child)).toBe(true)
    expect(shouldIgnoreHotkey(eventWithTarget(child))).toBe(true)
  })

  it("ignores composition, repeats, and previously handled events", () => {
    const target = document.createElement("button")
    const composing = eventWithTarget(target, { isComposing: true })
    const repeated = eventWithTarget(target, { repeat: true })
    const prevented = eventWithTarget(target)
    prevented.preventDefault()

    expect(shouldIgnoreHotkey(composing)).toBe(true)
    expect(shouldIgnoreHotkey(repeated)).toBe(true)
    expect(shouldIgnoreHotkey(prevented)).toBe(true)
  })
})

describe("rapid key runs", () => {
  it("fires on the third press inside the window", () => {
    const completesRun = createRapidKeyRun({ count: 3, windowMs: 750 })
    expect(completesRun(0)).toBe(false)
    expect(completesRun(200)).toBe(false)
    expect(completesRun(400)).toBe(true)
  })

  it("drops presses older than the window", () => {
    const completesRun = createRapidKeyRun({ count: 3, windowMs: 750 })
    expect(completesRun(0)).toBe(false)
    expect(completesRun(100)).toBe(false)
    expect(completesRun(900)).toBe(false)
    expect(completesRun(1000)).toBe(false)
    expect(completesRun(1100)).toBe(true)
  })

  it("starts a fresh run after firing", () => {
    const completesRun = createRapidKeyRun({ count: 3, windowMs: 750 })
    for (const at of [0, 100, 200]) completesRun(at)
    expect(completesRun(300)).toBe(false)
    expect(completesRun(400)).toBe(false)
    expect(completesRun(500)).toBe(true)
  })
})
