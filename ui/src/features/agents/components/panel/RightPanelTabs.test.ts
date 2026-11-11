/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { createElement } from "react"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { afterEach, describe, expect, it, vi } from "vitest"

import { productAnalytics } from "@/lib/analytics"
import {
  RightPanelTabs,
  surfaceShortcutActionForKey,
} from "@/features/agents/components/panel/RightPanelTabs"

vi.mock("@/lib/analytics", () => ({
  productAnalytics: { featureOpened: vi.fn() },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

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

it("emits adoption only for real panel gestures, not mounting or restoring tabs", () => {
  const onAddFiles = vi.fn()
  const onAddTerminal = vi.fn()
  const props = {
    mode: "inline" as const,
    surfaces: [],
    activeSurfaceId: null,
    pendingSurfaceIds: new Set<string>(),
    terminalLabelsById: new Map<string, string>(),
    onActivate: vi.fn(),
    onCloseSurface: vi.fn(),
    onCloseOtherSurfaces: vi.fn(),
    onCloseSurfacesToRight: vi.fn(),
    onCloseAllSurfaces: vi.fn(),
    onCopyFilePath: vi.fn(),
    onAddTerminal,
    onAddDiff: vi.fn(),
    onAddFiles,
    terminalAvailable: true,
    diffAvailable: true,
    children: null,
  }
  const view = render(
    createElement(TooltipProvider, null, createElement(RightPanelTabs, props))
  )
  expect(productAnalytics.featureOpened).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: /Files/ }))
  expect(onAddFiles).toHaveBeenCalledOnce()
  expect(productAnalytics.featureOpened).toHaveBeenLastCalledWith("files")
  fireEvent.click(screen.getByRole("button", { name: /Terminal/ }))
  expect(onAddTerminal).toHaveBeenCalledOnce()
  expect(productAnalytics.featureOpened).toHaveBeenLastCalledWith("terminal")
  expect(productAnalytics.featureOpened).toHaveBeenCalledTimes(2)
  view.rerender(
    createElement(
      TooltipProvider,
      null,
      createElement(RightPanelTabs, {
        ...props,
        surfaces: [{ id: "diff", kind: "diff" }],
        activeSurfaceId: "diff",
      })
    )
  )
  expect(productAnalytics.featureOpened).toHaveBeenCalledTimes(2)
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }))
  expect(props.onActivate).toHaveBeenCalledOnce()
  expect(productAnalytics.featureOpened).toHaveBeenLastCalledWith("diff")
})

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
