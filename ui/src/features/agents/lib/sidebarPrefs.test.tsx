/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { SIDEBAR_PREFS_STORAGE_KEY, useSidebarPrefs } from "./sidebarPrefs"

const NO_PROJECT_GROUP_KEY = "project:no-project"

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

function ProjectPinControl() {
  const { prefs, toggleProjectPin } = useSidebarPrefs()
  const pinned = prefs.pinnedProjectKeys.includes(NO_PROJECT_GROUP_KEY)
  return (
    <button
      type="button"
      onClick={() => toggleProjectPin(NO_PROJECT_GROUP_KEY)}
    >
      {pinned ? "Unpin No project" : "Pin No project"}
    </button>
  )
}

describe("sidebar project pins", () => {
  it("persists a project pin client-side", () => {
    render(<ProjectPinControl />)

    fireEvent.click(screen.getByRole("button", { name: "Pin No project" }))

    expect(
      screen.getByRole("button", { name: "Unpin No project" })
    ).toBeTruthy()
    expect(
      JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY) ?? "{}")
        .pinnedProjectKeys
    ).toContain(NO_PROJECT_GROUP_KEY)
  })
})
