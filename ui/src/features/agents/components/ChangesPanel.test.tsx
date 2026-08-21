/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import { changesEmptyLabel } from "./ChangesPanel"

describe("changesEmptyLabel", () => {
  it("distinguishes loading, missing, error, and empty changes", () => {
    expect(changesEmptyLabel({ isLoading: true })).toBe("Reading changes…")
    expect(changesEmptyLabel({ isLoading: false, status: "missing" })).toBe(
      "Changes are not available for this workspace."
    )
    expect(changesEmptyLabel({ isLoading: false, status: "error" })).toBe(
      "Could not read changes. Try refreshing."
    )
    expect(changesEmptyLabel({ isLoading: false, status: "ready" })).toBe(
      "No changes yet."
    )
  })

  it("surfaces request errors", () => {
    expect(
      changesEmptyLabel({
        isLoading: false,
        error: new Error("Sandbox unavailable"),
      })
    ).toBe("Sandbox unavailable")
  })
})
