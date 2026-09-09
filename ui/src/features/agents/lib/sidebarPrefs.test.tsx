/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import {
  DEFAULT_SIDEBAR_PREFS,
  SIDEBAR_PREFS_STORAGE_KEY,
  useSidebarPrefs,
} from "./sidebarPrefs"

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

function PreferenceControl() {
  const { prefs, setView } = useSidebarPrefs()
  return (
    <button
      type="button"
      onClick={() => setView({ recentsPlacement: "above-projects" })}
    >
      {prefs.recentsPlacement}
    </button>
  )
}

describe("sidebar recents placement preference", () => {
  it("defaults to the existing below-projects layout", () => {
    render(<PreferenceControl />)

    expect(
      screen.getByRole("button", {
        name: DEFAULT_SIDEBAR_PREFS.recentsPlacement,
      })
    ).toBeTruthy()
  })

  it("persists the above-projects layout client-side", () => {
    render(<PreferenceControl />)

    fireEvent.click(screen.getByRole("button", { name: "below-projects" }))

    expect(screen.getByRole("button", { name: "above-projects" })).toBeTruthy()
    expect(
      JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY) ?? "{}")
        .recentsPlacement
    ).toBe("above-projects")
  })
})
