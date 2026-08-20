import { describe, expect, it } from "vitest"

import { surfaceShortcutActionForKey } from "@/features/agents/components/panel/RightPanelTabs"

const actions = [
  { shortcut: "T", available: true },
  { shortcut: "D", available: true },
  { shortcut: "P", available: false },
] as const

function event(overrides: Partial<KeyboardEvent> = {}): KeyboardEvent {
  return {
    key: "t",
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
      surfaceShortcutActionForKey(actions, event({ key: "t" }))?.shortcut
    ).toBe("T")
    expect(
      surfaceShortcutActionForKey(actions, event({ key: "D" }))?.shortcut
    ).toBe("D")
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
