/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  isDesktopLocalModeEnabled,
} from "./desktop-local-mode"

beforeEach(() => {
  window.openSweDesktop = undefined
})

describe("desktop local mode", () => {
  it("is unavailable outside the desktop app", () => {
    expect(isDesktopLocalModeEnabled()).toBe(false)
  })

  it("reads local-only mode from the desktop process", () => {
    window.openSweDesktop = {
      isDesktop: true,
      localOnly: true,
    } as Window["openSweDesktop"]

    expect(isDesktopLocalModeEnabled()).toBe(true)
  })
})
