import { describe, expect, it } from "vitest"

import type { AgentTab, AgentTabsState } from "./tabs"
import {
  EMPTY_TABS_STATE,
  activateTab,
  closeTab,
  cloudTabId,
  openTab,
  renameTab,
  sanitizeTabsState,
  tabForPathname,
} from "./tabs"

function tab(id: string, overrides: Partial<AgentTab> = {}): AgentTab {
  return {
    id,
    kind: "cloud",
    path: `/agents/${id}`,
    title: id,
    ...overrides,
  }
}

function state(tabs: Array<AgentTab>, activeId: string | null): AgentTabsState {
  return { tabs, activeId }
}

describe("tabForPathname", () => {
  it("treats the agents index as the home screen", () => {
    expect(tabForPathname("/agents")).toBeNull()
    expect(tabForPathname("/agents/")).toBeNull()
  })

  it("maps a cloud thread and its plan view onto one tab", () => {
    expect(tabForPathname("/agents/abc")).toMatchObject({
      id: cloudTabId("abc"),
      kind: "cloud",
      path: "/agents/abc",
    })
    expect(tabForPathname("/agents/abc/plan")?.id).toBe(cloudTabId("abc"))
  })

  it("maps local sessions and the composer", () => {
    expect(tabForPathname("/agents/local/s1")).toMatchObject({
      id: "local:s1",
      kind: "local",
      path: "/agents/local/s1",
    })
    expect(tabForPathname("/agents/new")).toMatchObject({
      id: "new",
      kind: "new",
    })
  })

  it("gives the section pages no tab of their own", () => {
    expect(tabForPathname("/agents/reviews/o/r/1")).toBeNull()
    expect(tabForPathname("/agents/threads")).toBeNull()
    expect(tabForPathname("/agents/skills")).toBeNull()
  })
})

describe("openTab", () => {
  it("appends and activates a new tab", () => {
    const next = openTab(EMPTY_TABS_STATE, tab("a"))
    expect(next.tabs).toHaveLength(1)
    expect(next.activeId).toBe("a")
  })

  it("activates an existing tab instead of duplicating it", () => {
    const next = openTab(state([tab("a"), tab("b")], "b"), tab("a"))
    expect(next.tabs.map((item) => item.id)).toEqual(["a", "b"])
    expect(next.activeId).toBe("a")
  })

  it("keeps a renamed tab's title when the route re-opens it", () => {
    const opened = renameTab(openTab(EMPTY_TABS_STATE, tab("a")), "a", "Fix CI")
    expect(openTab(opened, tab("a")).tabs[0]?.title).toBe("Fix CI")
  })

  it("turns the active draft tab into the session it started", () => {
    const draft = openTab(EMPTY_TABS_STATE, {
      id: "new",
      kind: "new",
      path: "/agents/new",
      title: "New session",
    })
    const next = openTab(draft, tab("a"))
    expect(next.tabs.map((item) => item.id)).toEqual(["a"])
    expect(next.activeId).toBe("a")
  })

  it("leaves an unfocused draft tab alone", () => {
    const withDraft = state(
      [{ id: "new", kind: "new", path: "/agents/new", title: "New" }, tab("b")],
      "b"
    )
    const next = openTab(withDraft, tab("a"))
    expect(next.tabs.map((item) => item.id)).toEqual(["new", "b", "a"])
  })
})

describe("closeTab", () => {
  it("hands focus to the tab on the right", () => {
    const next = closeTab(state([tab("a"), tab("b"), tab("c")], "b"), "b")
    expect(next.activeId).toBe("c")
  })

  it("falls back to the tab on the left", () => {
    const next = closeTab(state([tab("a"), tab("b")], "b"), "b")
    expect(next.activeId).toBe("a")
  })

  it("returns to the home screen when the last tab closes", () => {
    expect(closeTab(state([tab("a")], "a"), "a").activeId).toBeNull()
  })

  it("keeps focus when closing a background tab", () => {
    const next = closeTab(state([tab("a"), tab("b")], "a"), "b")
    expect(next.activeId).toBe("a")
  })
})

describe("activateTab", () => {
  it("ignores ids that aren't open", () => {
    const current = state([tab("a")], "a")
    expect(activateTab(current, "missing")).toBe(current)
  })

  it("clears the active tab for the home screen", () => {
    expect(activateTab(state([tab("a")], "a"), null).activeId).toBeNull()
  })
})

describe("sanitizeTabsState", () => {
  it("drops malformed entries and external paths", () => {
    const next = sanitizeTabsState({
      tabs: [
        tab("a"),
        { id: "b", kind: "cloud", path: "https://evil.example", title: "b" },
        { id: "c", kind: "bogus", path: "/agents/c", title: "c" },
        { id: "a", kind: "cloud", path: "/agents/a", title: "dupe" },
        null,
      ],
      activeId: "b",
    })
    expect(next.tabs.map((item) => item.id)).toEqual(["a"])
    expect(next.activeId).toBeNull()
  })

  it("returns an empty state for junk", () => {
    expect(sanitizeTabsState(null)).toEqual(EMPTY_TABS_STATE)
    expect(sanitizeTabsState({ tabs: "nope" })).toEqual(EMPTY_TABS_STATE)
  })
})
