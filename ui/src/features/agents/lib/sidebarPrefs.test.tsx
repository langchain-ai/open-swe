/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { SIDEBAR_PREFS_STORAGE_KEY, useSidebarPrefs } from "./sidebarPrefs"

const NO_REPO_GROUP_KEY = "repo:no-repo"

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

function RepoPinControl() {
  const { prefs, toggleRepoPin } = useSidebarPrefs()
  const pinned = prefs.pinnedRepoKeys.includes(NO_REPO_GROUP_KEY)
  return (
    <button type="button" onClick={() => toggleRepoPin(NO_REPO_GROUP_KEY)}>
      {pinned ? "Unpin No repository" : "Pin No repository"}
    </button>
  )
}

describe("sidebar repository pins", () => {
  it("persists a repository pin client-side", () => {
    render(<RepoPinControl />)

    fireEvent.click(screen.getByRole("button", { name: "Pin No repository" }))

    expect(
      screen.getByRole("button", { name: "Unpin No repository" })
    ).toBeTruthy()
    expect(
      JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY) ?? "{}")
        .pinnedRepoKeys
    ).toContain(NO_REPO_GROUP_KEY)
  })
})

function OrganizeAndPins() {
  const { prefs } = useSidebarPrefs()
  return (
    <output>
      {[
        prefs.organize,
        ...prefs.pinnedRepoKeys,
        ...prefs.collapsedRepoKeys,
        ...prefs.collapsedSectionKeys,
      ].join(" ")}
    </output>
  )
}

describe("preferences saved while repositories were called projects", () => {
  it("reads them under their repository names", () => {
    window.localStorage.setItem(
      SIDEBAR_PREFS_STORAGE_KEY,
      JSON.stringify({
        organize: "project",
        pinnedProjectKeys: ["project:no-project", "project:acme/api"],
        collapsedProjectKeys: ["project:acme/oss"],
        collapsedSectionKeys: ["projects", "recents"],
      })
    )

    render(<OrganizeAndPins />)

    expect(screen.getByRole("status").textContent).toBe(
      "repo repo:no-repo repo:acme/api repo:acme/oss repos recents"
    )
  })
})

describe("sidebar date grouping", () => {
  it.each([undefined, "none", "date", "repo", "workspace"])(
    "defaults to Date while preserving saved grouping %s",
    (organize) => {
      window.localStorage.setItem(
        SIDEBAR_PREFS_STORAGE_KEY,
        JSON.stringify({ organize })
      )
      render(<OrganizeAndPins />)
      expect(screen.getByRole("status").textContent).toBe(organize ?? "date")
    }
  )
})
