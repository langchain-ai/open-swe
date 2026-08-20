/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  readStoredPanelCollapsed,
  writeStoredPanelCollapsed,
} from "./gitPanelPreferences"

beforeEach(() => window.localStorage.clear())

describe("git panel collapsed preference", () => {
  it("defaults each thread to collapsed", () => {
    expect(readStoredPanelCollapsed("thread-a")).toBe(true)
    expect(readStoredPanelCollapsed("thread-b")).toBe(true)
  })

  it("keeps panel state per thread", () => {
    writeStoredPanelCollapsed("thread-a", false)

    expect(readStoredPanelCollapsed("thread-a")).toBe(false)
    expect(readStoredPanelCollapsed("thread-b")).toBe(true)
  })
})
