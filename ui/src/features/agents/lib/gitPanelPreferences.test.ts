/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "./gitPanelPreferences"

function mockViewport(matches: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

beforeEach(() => {
  window.localStorage.clear()
  mockViewport(false)
})

describe("git panel collapsed preference", () => {
  it("defaults to collapsed before really wide screens", () => {
    mockViewport(false)

    expect(readStoredPanelCollapsed()).toBe(true)
  })

  it("defaults to expanded on really wide screens", () => {
    mockViewport(true)

    expect(readStoredPanelCollapsed()).toBe(false)
  })

  it("keeps an explicit collapsed preference on really wide screens", () => {
    mockViewport(true)
    writeStoredPanelCollapsed(true)

    expect(readStoredPanelCollapsed()).toBe(true)
  })

  it("keeps an explicit expanded preference before really wide screens", () => {
    mockViewport(false)
    writeStoredPanelCollapsed(false)

    expect(readStoredPanelCollapsed()).toBe(false)
  })
})
