import { beforeEach, describe, expect, it } from "vitest"

import {
  migratePersistedDiffPanelState,
  selectThreadDiffScope,
  useDiffPanelStore,
} from "@/features/agents/lib/diffPanelStore"

const ref = { scope: "cloud" as const, threadId: "thread-1" }
const other = { scope: "local" as const, threadId: "thread-1" }

describe("diffPanelStore", () => {
  beforeEach(() => {
    useDiffPanelStore.setState({ byThreadKey: {} })
  })

  it("defaults to branch changes only when they are available", () => {
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, true)).toBe("branch")
    expect(selectThreadDiffScope(byThreadKey, ref, false)).toBe("working-tree")
    expect(selectThreadDiffScope(byThreadKey, null, true)).toBe("branch")
  })

  it("keeps a selectable branch scope off the default when asked", () => {
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, true, false)).toBe(
      "working-tree"
    )
    useDiffPanelStore.getState().selectScope(ref, "branch")
    expect(
      selectThreadDiffScope(
        useDiffPanelStore.getState().byThreadKey,
        ref,
        true,
        false
      )
    ).toBe("branch")
  })

  it("remembers an explicit scope per thread", () => {
    useDiffPanelStore.getState().selectScope(ref, "working-tree")
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, true)).toBe("working-tree")
    // Same thread id under a different scope is a different panel.
    expect(selectThreadDiffScope(byThreadKey, other, true)).toBe("branch")
  })

  it("falls back to the working tree when branch changes are unavailable", () => {
    useDiffPanelStore.getState().selectScope(ref, "branch")
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, false)).toBe("working-tree")
  })

  it("drops a thread's selection", () => {
    useDiffPanelStore.getState().selectScope(ref, "working-tree")
    useDiffPanelStore.getState().removeThread(ref)
    expect(useDiffPanelStore.getState().byThreadKey).toEqual({})
  })

  it("ignores malformed persisted state", () => {
    expect(migratePersistedDiffPanelState(null)).toEqual({ byThreadKey: {} })
    expect(migratePersistedDiffPanelState("nope")).toEqual({ byThreadKey: {} })
    expect(migratePersistedDiffPanelState({ byThreadKey: 7 })).toEqual({
      byThreadKey: {},
    })
  })

  it("drops persisted entries whose kind is unknown, and maps the v1 names", () => {
    expect(
      migratePersistedDiffPanelState({
        byThreadKey: {
          "cloud:a": { kind: "pull-request" },
          "cloud:b": { kind: "unstaged" },
          "cloud:c": null,
          "cloud:d": { kind: "thread", extra: "ignored" },
          "cloud:e": { kind: "branch" },
          "cloud:f": { kind: "working-tree" },
        },
      })
    ).toEqual({
      byThreadKey: {
        "cloud:a": { kind: "branch" },
        "cloud:d": { kind: "working-tree" },
        "cloud:e": { kind: "branch" },
        "cloud:f": { kind: "working-tree" },
      },
    })
  })
})
