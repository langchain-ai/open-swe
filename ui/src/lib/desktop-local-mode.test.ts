/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  DESKTOP_LOCAL_MODE_STORAGE_KEY,
  enableDesktopLocalMode,
  isDesktopLocalModeEnabled,
} from "./desktop-local-mode"
import { readStoredDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"

beforeEach(() => {
  const values = new Map<string, string>()
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  })
  window.__OPEN_SWE_LOCAL__ = undefined
})

describe("desktop local mode", () => {
  it("is unavailable on a hosted deployment", () => {
    window.localStorage.setItem(DESKTOP_LOCAL_MODE_STORAGE_KEY, "true")
    expect(isDesktopLocalModeEnabled()).toBe(false)
  })

  it("persists local-only mode and selects This Mac", () => {
    window.__OPEN_SWE_LOCAL__ = true

    enableDesktopLocalMode()

    expect(isDesktopLocalModeEnabled()).toBe(true)
    expect(window.localStorage.getItem(DESKTOP_LOCAL_MODE_STORAGE_KEY)).toBe(
      "true"
    )
    expect(readStoredDesktopThreadSource()).toBe("local")
  })
})
