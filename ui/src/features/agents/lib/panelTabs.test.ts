import { describe, expect, it } from "vitest"

import { closePanelTab, openPanelTab, syncTerminalTabs } from "./panelTabs"

describe("panel tabs", () => {
  it("focuses an existing single-instance tab instead of duplicating it", () => {
    const opened = openPanelTab(
      openPanelTab(
        { tabs: [], activeTabId: null },
        { id: "review", kind: "review" }
      ),
      { id: "term-a", kind: "terminal" }
    )
    const reopened = openPanelTab(opened, { id: "review", kind: "review" })

    expect(reopened.tabs).toHaveLength(2)
    expect(reopened.activeTabId).toBe("review")
  })

  it("activates a neighbour when the active tab closes", () => {
    const state = {
      tabs: [
        { id: "review", kind: "review" as const },
        { id: "group-term-1", kind: "terminal" as const },
      ],
      activeTabId: "group-term-1",
    }

    expect(closePanelTab(state, "group-term-1").activeTabId).toBe("review")
    expect(syncTerminalTabs(state, []).tabs).toEqual([state.tabs[0]])
  })
})
