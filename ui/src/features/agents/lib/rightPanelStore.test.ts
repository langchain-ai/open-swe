/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import {
  closeRightPanelSurface,
  normalizeRightPanelState,
  upsertRightPanelSurface,
  type RightPanelState,
} from "./rightPanelStore"

const empty: RightPanelState = {
  isOpen: false,
  activeSurfaceId: null,
  surfaces: [],
}

describe("right panel store", () => {
  it("keeps ordered singleton surfaces and activates reopened entries", () => {
    const withDiff = upsertRightPanelSurface(empty, {
      id: "diff",
      kind: "diff",
    })
    const withFiles = upsertRightPanelSurface(withDiff, {
      id: "files",
      kind: "files",
    })
    const reopened = upsertRightPanelSurface(withFiles, {
      id: "diff",
      kind: "diff",
    })

    expect(reopened.surfaces.map((surface) => surface.id)).toEqual([
      "diff",
      "files",
    ])
    expect(reopened.activeSurfaceId).toBe("diff")
  })

  it("selects the adjacent surface when the active surface closes", () => {
    const state: RightPanelState = {
      isOpen: true,
      activeSurfaceId: "files",
      surfaces: [
        { id: "diff", kind: "diff" },
        { id: "files", kind: "files" },
        { id: "pull-request", kind: "pull-request" },
      ],
    }

    expect(closeRightPanelSurface(state, "files").activeSurfaceId).toBe(
      "pull-request"
    )
  })

  it("drops malformed persisted surfaces and repairs selection", () => {
    expect(
      normalizeRightPanelState({
        isOpen: true,
        activeSurfaceId: "missing",
        surfaces: [
          { id: "diff", kind: "diff" },
          { id: "file:bad", kind: "file" },
        ],
      })
    ).toEqual({
      isOpen: true,
      activeSurfaceId: "diff",
      surfaces: [{ id: "diff", kind: "diff" }],
    })
  })
})
