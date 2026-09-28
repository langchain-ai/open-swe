import { describe, expect, it } from "vitest"

import {
  CDP_MODIFIER_CTRL,
  CDP_MODIFIER_META,
  CDP_MODIFIER_SHIFT,
  cdpKeyEvents,
  windowsVirtualKeyCode,
} from "@/features/agents/browser/cdp/keyboardCodes"

const key = (overrides: Partial<Parameters<typeof cdpKeyEvents>[0]>) => ({
  key: "a",
  code: "KeyA",
  repeat: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  ...overrides,
})

describe("cdp keyboard translation", () => {
  it("resolves virtual key codes for letters, digits, and named keys", () => {
    expect(windowsVirtualKeyCode("KeyA", "a")).toBe(65)
    expect(windowsVirtualKeyCode("Digit7", "7")).toBe(55)
    expect(windowsVirtualKeyCode("Numpad3", "3")).toBe(99)
    expect(windowsVirtualKeyCode("F5", "F5")).toBe(116)
    expect(windowsVirtualKeyCode("ArrowLeft", "ArrowLeft")).toBe(37)
    expect(windowsVirtualKeyCode("Unknown", "z")).toBe(90)
    expect(windowsVirtualKeyCode("Unknown", "Dead")).toBe(0)
  })

  it("sends printable keys with text and shortcuts as raw key downs", () => {
    const printable = cdpKeyEvents(key({}), false)
    expect(printable.down).toMatchObject({
      type: "keyDown",
      text: "a",
      modifiers: 0,
    })
    expect(printable.up.type).toBe("keyUp")
    const shifted = cdpKeyEvents(key({ key: "A", shiftKey: true }), false)
    expect(shifted.down).toMatchObject({
      type: "keyDown",
      text: "A",
      modifiers: CDP_MODIFIER_SHIFT,
    })
    const shortcut = cdpKeyEvents(key({ ctrlKey: true }), false)
    expect(shortcut.down).toMatchObject({
      type: "rawKeyDown",
      modifiers: CDP_MODIFIER_CTRL,
    })
    expect(shortcut.down.text).toBeUndefined()
  })

  it("maps Command to Control for the Linux browser only when asked", () => {
    expect(cdpKeyEvents(key({ metaKey: true }), true).down.modifiers).toBe(
      CDP_MODIFIER_CTRL
    )
    expect(cdpKeyEvents(key({ metaKey: true }), false).down.modifiers).toBe(
      CDP_MODIFIER_META
    )
  })

  it("marks numpad keys and carries Enter as a carriage return", () => {
    const enter = cdpKeyEvents(
      key({ key: "Enter", code: "NumpadEnter" }),
      false
    )
    expect(enter.down).toMatchObject({
      isKeypad: true,
      location: 3,
      text: "\r",
    })
  })
})
