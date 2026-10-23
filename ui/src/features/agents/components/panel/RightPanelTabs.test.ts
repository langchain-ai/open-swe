import { describe, expect, it } from "vitest"

import { surfaceShortcutActionForKey } from "@/features/agents/components/panel/RightPanelTabs"

const actions = [
  { shortcut: undefined, available: true },
  { shortcut: "F", available: true },
  { shortcut: "B", available: true },
  { shortcut: "P", available: false },
] as const

function event(overrides: Partial<KeyboardEvent> = {}): KeyboardEvent {
  return {
    key: "f",
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    isComposing: false,
    defaultPrevented: false,
    ...overrides,
  } as KeyboardEvent
}

describe("surfaceShortcutActionForKey", () => {
  it("matches a shortcut case-insensitively", () => {
    expect(
      surfaceShortcutActionForKey(actions, event({ key: "f" }))?.shortcut
    ).toBe("F")
    expect(
      surfaceShortcutActionForKey(actions, event({ key: "B" }))?.shortcut
    ).toBe("B")
    expect(surfaceShortcutActionForKey(actions, event({ key: "t" }))).toBeNull()
    expect(surfaceShortcutActionForKey(actions, event({ key: "d" }))).toBeNull()
  })

  it("ignores unavailable surfaces", () => {
    expect(surfaceShortcutActionForKey(actions, event({ key: "p" }))).toBeNull()
  })

  it("ignores modified, composing, and already-handled keys", () => {
    expect(
      surfaceShortcutActionForKey(actions, event({ metaKey: true }))
    ).toBeNull()
    expect(
      surfaceShortcutActionForKey(actions, event({ ctrlKey: true }))
    ).toBeNull()
    expect(
      surfaceShortcutActionForKey(actions, event({ altKey: true }))
    ).toBeNull()
    expect(
      surfaceShortcutActionForKey(actions, event({ isComposing: true }))
    ).toBeNull()
    expect(
      surfaceShortcutActionForKey(actions, event({ defaultPrevented: true }))
    ).toBeNull()
  })
})
