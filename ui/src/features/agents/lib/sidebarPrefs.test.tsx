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

function SubagentControl() {
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
  return (
    <button
      type="button"
      onClick={() => toggleSubagentsCollapsed("cloud:parent")}
    >
      {prefs.expandedSubagentKeys.includes("cloud:parent")
        ? "Expanded"
        : "Collapsed"}
    </button>
  )
}

describe("sidebar subagent expansion", () => {
  it("starts collapsed and persists explicit expansion", () => {
    const view = render(<SubagentControl />)
    fireEvent.click(screen.getByRole("button", { name: "Collapsed" }))
    expect(screen.getByRole("button", { name: "Expanded" })).toBeTruthy()
    expect(
      JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY) ?? "{}")
        .expandedSubagentKeys
    ).toEqual(["cloud:parent"])
    view.unmount()
    render(<SubagentControl />)
    fireEvent.click(screen.getByRole("button", { name: "Expanded" }))
    expect(screen.getByRole("button", { name: "Collapsed" })).toBeTruthy()
  })

  it.each([true, false])(
    "migrates saved folds with collapse default %s",
    (collapseSubagentsByDefault) => {
      window.localStorage.setItem(
        SIDEBAR_PREFS_STORAGE_KEY,
        JSON.stringify({
          collapseSubagentsByDefault,
          collapsedSubagentKeys: ["cloud:parent"],
        })
      )
      render(<SubagentControl />)
      expect(
        screen.getByRole("button", {
          name: collapseSubagentsByDefault ? "Expanded" : "Collapsed",
        })
      ).toBeTruthy()
    }
  )
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
