/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  CHANGES_TAB,
  closePanelTab,
  openPanelTab,
  readPanelTabs,
  syncTerminalTabs,
  writePanelTabs,
} from "./panelTabs"

beforeEach(() => window.localStorage.clear())

describe("panel tabs", () => {
  it("focuses an existing single-instance tab instead of duplicating it", () => {
    const opened = openPanelTab(
      openPanelTab({ tabs: [], activeTabId: null }, CHANGES_TAB),
      { id: "term-a", kind: "terminal" }
    )
    const reopened = openPanelTab(opened, CHANGES_TAB)

    expect(reopened.tabs).toHaveLength(2)
    expect(reopened.activeTabId).toBe("changes")
  })

  it("activates a neighbour when the active tab closes", () => {
    const state = {
      tabs: [CHANGES_TAB, { id: "group-term-1", kind: "terminal" as const }],
      activeTabId: "group-term-1",
    }

    expect(closePanelTab(state, "group-term-1").activeTabId).toBe("changes")
    expect(syncTerminalTabs(state, []).tabs).toEqual([CHANGES_TAB])
  })

  it("migrates the legacy review tab and active id", () => {
    window.localStorage.setItem(
      "open-swe.panel-tabs.v1:thread-1",
      JSON.stringify({
        tabs: [{ id: "review", kind: "review" }],
        activeTabId: "review",
      })
    )

    expect(readPanelTabs("thread-1", [CHANGES_TAB])).toEqual({
      tabs: [CHANGES_TAB],
      activeTabId: "changes",
    })
  })

  it("persists Changes selection and rejects an invalid empty active id", () => {
    writePanelTabs("thread-1", {
      tabs: [CHANGES_TAB, { id: "term-a", kind: "terminal" }],
      activeTabId: "",
    })

    expect(readPanelTabs("thread-1", [CHANGES_TAB]).activeTabId).toBe("changes")

    writePanelTabs("thread-1", {
      tabs: [CHANGES_TAB, { id: "term-a", kind: "terminal" }],
      activeTabId: "changes",
    })
    expect(readPanelTabs("thread-1", [CHANGES_TAB]).activeTabId).toBe("changes")
  })
})
