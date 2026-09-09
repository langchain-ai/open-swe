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

function SortControl() {
  const { prefs, setView } = useSidebarPrefs()
  return (
    <button type="button" onClick={() => setView({ sortChats: "updated" })}>
      {prefs.sortChats}
    </button>
  )
}

describe("sidebar chat sorting", () => {
  it("defaults to creation time and persists changes", () => {
    render(<SortControl />)

    const control = screen.getByRole("button", { name: "created" })
    fireEvent.click(control)

    expect(screen.getByRole("button", { name: "updated" })).toBeTruthy()
    expect(
      JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY) ?? "{}")
        .sortChats
    ).toBe("updated")
  })
})

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
