import { beforeEach, describe, expect, it } from "vitest"

import {
  migratePersistedRightPanelState,
  selectActiveRightPanelSurface,
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"

const ref = { scope: "cloud" as const, threadId: "thread-1" }
const state = () =>
  selectThreadRightPanelState(useRightPanelStore.getState().byThreadKey, ref)

describe("right panel store", () => {
  beforeEach(() => {
    useRightPanelStore.setState({ byThreadKey: {} })
  })

  it("opens a singleton surface once and keeps it active", () => {
    const { open } = useRightPanelStore.getState()
    open(ref, "diff")
    open(ref, "diff")
    expect(state().surfaces.map((surface) => surface.id)).toEqual(["diff"])
    expect(state().activeSurfaceId).toBe("diff")
    expect(state().isOpen).toBe(true)
  })

  it("keeps terminals as peer tabs and falls back when the active one closes", () => {
    const { openTerminal, closeSurface } = useRightPanelStore.getState()
    openTerminal(ref, "group-a")
    openTerminal(ref, "group-b")
    expect(state().activeSurfaceId).toBe("terminal:group-b")
    closeSurface(ref, "terminal:group-b")
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "terminal:group-a",
    ])
    expect(state().activeSurfaceId).toBe("terminal:group-a")
  })

  it("drops terminal surfaces whose group no longer exists", () => {
    const { openTerminal, open, reconcileTerminalSurfaces } =
      useRightPanelStore.getState()
    open(ref, "diff")
    openTerminal(ref, "group-a")
    openTerminal(ref, "group-b")
    reconcileTerminalSurfaces(ref, ["group-a"])
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "diff",
      "terminal:group-a",
    ])
    // The active surface was reconciled away, so the panel re-points at a survivor.
    expect(
      state().surfaces.some((surface) => surface.id === state().activeSurfaceId)
    ).toBe(true)
  })

  it("adds agent-opened browser tabs without stealing focus and replaces the placeholder on activation", () => {
    const { open, openBrowser } = useRightPanelStore.getState()
    open(ref, "diff")
    openBrowser(ref, "agent-tab", { activate: false })
    expect(state().activeSurfaceId).toBe("diff")
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "diff",
      "browser:agent-tab",
    ])
    openBrowser(ref, null)
    expect(state().activeSurfaceId).toBe("browser:new")
    openBrowser(ref, "typed-tab")
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "diff",
      "browser:agent-tab",
      "browser:typed-tab",
    ])
    expect(state().activeSurfaceId).toBe("browser:typed-tab")
  })

  it("drops browser surfaces whose tab is gone but keeps the placeholder", () => {
    const { openBrowser, reconcileBrowserSurfaces } =
      useRightPanelStore.getState()
    openBrowser(ref, "a")
    openBrowser(ref, "b")
    openBrowser(ref, null)
    reconcileBrowserSurfaces(ref, ["b"])
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "browser:b",
      "browser:new",
    ])
    expect(state().activeSurfaceId).toBe("browser:new")
    reconcileBrowserSurfaces(ref, [])
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "browser:new",
    ])
    expect(state().activeSurfaceId).toBe("browser:new")
  })

  it("closes others and to the right relative to a surface", () => {
    const { open, openTerminal, closeSurfacesToRight, closeOtherSurfaces } =
      useRightPanelStore.getState()
    open(ref, "diff")
    openTerminal(ref, "group-a")
    openTerminal(ref, "group-b")
    closeSurfacesToRight(ref, "terminal:group-a")
    expect(state().surfaces.map((surface) => surface.id)).toEqual([
      "diff",
      "terminal:group-a",
    ])
    closeOtherSurfaces(ref, "diff")
    expect(state().surfaces.map((surface) => surface.id)).toEqual(["diff"])
  })

  it("hides the panel when the last surface closes", () => {
    const { open, closeSurface } = useRightPanelStore.getState()
    open(ref, "diff")
    closeSurface(ref, "diff")
    expect(state().surfaces).toEqual([])
    expect(state().isOpen).toBe(false)
    expect(
      selectActiveRightPanelSurface(
        useRightPanelStore.getState().byThreadKey,
        ref
      )
    ).toBeNull()
  })

  it("scopes surfaces per thread and per scope", () => {
    const { open, openTerminal } = useRightPanelStore.getState()
    open(ref, "diff")
    openTerminal({ scope: "local", threadId: "thread-1" }, "group-a")
    expect(state().surfaces.map((surface) => surface.id)).toEqual(["diff"])
    expect(
      selectThreadRightPanelState(useRightPanelStore.getState().byThreadKey, {
        scope: "local",
        threadId: "thread-1",
      }).surfaces.map((surface) => surface.id)
    ).toEqual(["terminal:group-a"])
  })
})

describe("migratePersistedRightPanelState", () => {
  it("returns an empty map for anything that is not persisted state", () => {
    expect(migratePersistedRightPanelState(null)).toEqual({ byThreadKey: {} })
    expect(migratePersistedRightPanelState("nope")).toEqual({ byThreadKey: {} })
    expect(migratePersistedRightPanelState({})).toEqual({ byThreadKey: {} })
    expect(migratePersistedRightPanelState({ byThreadKey: 7 })).toEqual({
      byThreadKey: {},
    })
  })

  it("drops surfaces with unknown kinds or missing fields", () => {
    const migrated = migratePersistedRightPanelState({
      byThreadKey: {
        "cloud:thread-1": {
          isOpen: true,
          activeSurfaceId: "diff",
          surfaces: [
            { id: "diff", kind: "diff" },
            { id: "evil", kind: "script" },
            { id: "file:", kind: "file" },
            // The retired pull-request surface, well formed as it was persisted.
            {
              id: "pull-request:acme%2Fapp:1",
              kind: "pull-request",
              repository: "acme/app",
              number: 1,
            },
          ],
        },
      },
    })
    expect(migrated.byThreadKey["cloud:thread-1"]?.surfaces).toEqual([
      { id: "diff", kind: "diff" },
    ])
  })

  it("never reopens a thread whose surfaces were all dropped", () => {
    const migrated = migratePersistedRightPanelState({
      byThreadKey: {
        "cloud:thread-1": {
          isOpen: true,
          activeSurfaceId: "evil",
          surfaces: [{ id: "evil", kind: "script" }],
        },
      },
    })
    expect(migrated.byThreadKey["cloud:thread-1"]).toEqual({
      isOpen: false,
      surfaces: [],
      activeSurfaceId: null,
    })
  })
})
