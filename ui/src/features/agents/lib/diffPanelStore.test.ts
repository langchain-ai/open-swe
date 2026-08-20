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

  it("defaults to the pull request scope only when the thread has one", () => {
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, true)).toBe("pull-request")
    expect(selectThreadDiffScope(byThreadKey, ref, false)).toBe("thread")
    expect(selectThreadDiffScope(byThreadKey, null, true)).toBe("pull-request")
  })

  it("remembers an explicit scope per thread", () => {
    useDiffPanelStore.getState().selectScope(ref, "thread")
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, true)).toBe("thread")
    // Same thread id under a different scope is a different panel.
    expect(selectThreadDiffScope(byThreadKey, other, true)).toBe("pull-request")
  })

  it("falls back to thread changes when the stored PR scope is unavailable", () => {
    useDiffPanelStore.getState().selectScope(ref, "pull-request")
    const { byThreadKey } = useDiffPanelStore.getState()
    expect(selectThreadDiffScope(byThreadKey, ref, false)).toBe("thread")
  })

  it("drops a thread's selection", () => {
    useDiffPanelStore.getState().selectScope(ref, "thread")
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

  it("drops persisted entries whose kind is unknown", () => {
    expect(
      migratePersistedDiffPanelState({
        byThreadKey: {
          "cloud:a": { kind: "pull-request" },
          "cloud:b": { kind: "unstaged" },
          "cloud:c": null,
          "cloud:d": { kind: "thread", extra: "ignored" },
        },
      })
    ).toEqual({
      byThreadKey: {
        "cloud:a": { kind: "pull-request" },
        "cloud:d": { kind: "thread" },
      },
    })
  })
})
