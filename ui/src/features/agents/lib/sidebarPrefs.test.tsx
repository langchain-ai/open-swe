/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SIDEBAR_PREFS_STORAGE_KEY, useSidebarPrefs } from "./sidebarPrefs"
import { cloudProjectAliases, sidebarProjectKey } from "./sidebarThreads"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe("shared sidebar preferences", () => {
  it.each([
    ["local", sidebarProjectKey("/Users/johannes/Dev/open-swe")!],
    ["cloud", sidebarProjectKey("langchain-ai/open-swe")!],
    [
      "aliased local",
      cloudProjectAliases([
        { name: "open-swe", repoFullName: "langchain-ai/open-swe" },
      ]).get("open-swe")!,
    ],
  ])(
    "shares %s project pins between sidebar and popover and persists them",
    (_, key) => {
      const sidebar = renderHook(() => useSidebarPrefs())
      const popover = renderHook(() => useSidebarPrefs())

      act(() => popover.result.current.toggleProjectPin(key))
      expect(sidebar.result.current.prefs.pinnedProjectKeys).toEqual([key])
      expect(popover.result.current.prefs.pinnedProjectKeys).toEqual([key])

      act(() => sidebar.result.current.setCompact(true))
      expect(popover.result.current.prefs.compact).toBe(true)
      expect(
        JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY)!)
      ).toMatchObject({
        compact: true,
        pinnedProjectKeys: [key],
      })

      sidebar.unmount()
      popover.unmount()
      const reopened = renderHook(() => useSidebarPrefs())
      expect(reopened.result.current.prefs.pinnedProjectKeys).toEqual([key])
      const sidebarAgain = renderHook(() => useSidebarPrefs())
      act(() => sidebarAgain.result.current.toggleProjectPin(key))
      expect(reopened.result.current.prefs.pinnedProjectKeys).toEqual([])
      expect(
        JSON.parse(window.localStorage.getItem(SIDEBAR_PREFS_STORAGE_KEY)!)
      ).toMatchObject({
        compact: true,
        pinnedProjectKeys: [],
      })
    }
  )

  it("loads persisted pins and synchronizes storage changes from another window", () => {
    window.localStorage.setItem(
      SIDEBAR_PREFS_STORAGE_KEY,
      JSON.stringify({ pinnedProjectKeys: ["project:acme/api"] })
    )
    const sidebar = renderHook(() => useSidebarPrefs())
    const popover = renderHook(() => useSidebarPrefs())
    expect(sidebar.result.current.prefs.pinnedProjectKeys).toEqual([
      "project:acme/api",
    ])

    act(() => {
      window.localStorage.removeItem(SIDEBAR_PREFS_STORAGE_KEY)
      window.dispatchEvent(
        new StorageEvent("storage", { key: SIDEBAR_PREFS_STORAGE_KEY })
      )
    })
    expect(sidebar.result.current.prefs.pinnedProjectKeys).toEqual([])
    expect(popover.result.current.prefs.pinnedProjectKeys).toEqual([])
  })

  it("shares updates even when persistence is unavailable", () => {
    const sidebar = renderHook(() => useSidebarPrefs())
    const popover = renderHook(() => useSidebarPrefs())
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Quota exceeded")
    })

    act(() => popover.result.current.toggleProjectPin("project:acme/api"))
    expect(sidebar.result.current.prefs.pinnedProjectKeys).toEqual([
      "project:acme/api",
    ])
    act(() => sidebar.result.current.toggleProjectPin("project:acme/api"))
    expect(popover.result.current.prefs.pinnedProjectKeys).toEqual([])
  })
})
