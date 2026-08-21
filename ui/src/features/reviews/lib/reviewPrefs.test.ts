/** @vitest-environment jsdom */

import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"

import {
  REVIEW_PANEL_DEFAULT_WIDTH,
  REVIEW_PANEL_MIN_WIDTH,
  clampReviewPanelWidth,
  readDiffStyle,
  readFindingsKey,
  readReviewPanelWidth,
  readReviewView,
  useDiffStylePref,
  useReadFindings,
  useReviewPanelWidth,
  useReviewViewPref,
  useViewedFiles,
  viewedFilesKey,
} from "./reviewPrefs"

const VIEWPORT = 1200
// clampReviewPanelWidth keeps 480px for the PR content column.
const MAX_WIDTH = VIEWPORT - 480

beforeEach(() => {
  window.localStorage.clear()
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: VIEWPORT,
  })
})

describe("diff style preference", () => {
  it("defaults to unified", () => {
    expect(readDiffStyle()).toBe("unified")
  })

  it("remembers the picked style across PRs", () => {
    const { result } = renderHook(() => useDiffStylePref())

    act(() => result.current[1]("split"))

    expect(result.current[0]).toBe("split")
    expect(readDiffStyle()).toBe("split")
  })

  it("ignores an unrecognized stored style", () => {
    window.localStorage.setItem("open-swe.review.diffStyle", "sideways")

    expect(readDiffStyle()).toBe("unified")
  })
})

describe("sidebar view preference", () => {
  it("has no opinion until the user picks one", () => {
    expect(readReviewView()).toBeNull()
  })

  it("remembers an explicit pick", () => {
    const { result } = renderHook(() => useReviewViewPref())

    act(() => result.current[1]("files"))

    expect(result.current[0]).toBe("files")
    expect(readReviewView()).toBe("files")
  })

  it("ignores an unrecognized stored view", () => {
    window.localStorage.setItem("open-swe.review.view", "agenda")

    expect(readReviewView()).toBeNull()
  })
})

describe("viewed files", () => {
  it("keys the set by pull request and head commit", () => {
    expect(viewedFilesKey("acme", "repo", 7, "abc")).toBe(
      "open-swe.review.viewed.acme/repo/7.abc"
    )
  })

  it("ticks a file off and back on", () => {
    const { result } = renderHook(() =>
      useViewedFiles("acme", "repo", 7, "abc")
    )

    act(() => result.current.setFileViewed("src/a.ts", true))
    expect([...result.current.viewed]).toEqual(["src/a.ts"])

    act(() => result.current.setFileViewed("src/a.ts", false))
    expect([...result.current.viewed]).toEqual([])
  })

  it("does not carry the set to a new head commit", () => {
    const first = renderHook(() => useViewedFiles("acme", "repo", 7, "abc"))
    act(() => first.result.current.setFileViewed("src/a.ts", true))

    const second = renderHook(() => useViewedFiles("acme", "repo", 7, "def"))

    expect([...second.result.current.viewed]).toEqual([])
  })

  it("restores the set for the same head commit", () => {
    const first = renderHook(() => useViewedFiles("acme", "repo", 7, "abc"))
    act(() => first.result.current.setFileViewed("src/a.ts", true))

    const second = renderHook(() => useViewedFiles("acme", "repo", 7, "abc"))

    expect([...second.result.current.viewed]).toEqual(["src/a.ts"])
  })
})

describe("read findings", () => {
  it("keys the set by review thread", () => {
    expect(readFindingsKey("thread-1")).toBe("open-swe.review.read.thread-1")
  })

  it("marks one finding read", () => {
    const { result } = renderHook(() => useReadFindings("thread-1"))

    act(() => result.current.markRead("f1"))

    expect([...result.current.read]).toEqual(["f1"])
  })

  it("marks every finding read at once", () => {
    const { result } = renderHook(() => useReadFindings("thread-1"))

    act(() => result.current.markAllRead(["f1", "f2"]))

    expect([...result.current.read]).toEqual(["f1", "f2"])
    expect([
      ...renderHook(() => useReadFindings("thread-1")).result.current.read,
    ]).toEqual(["f1", "f2"])
  })

  it("keeps each review thread's set separate", () => {
    const first = renderHook(() => useReadFindings("thread-1"))
    act(() => first.result.current.markRead("f1"))

    const second = renderHook(() => useReadFindings("thread-2"))

    expect([...second.result.current.read]).toEqual([])
  })
})

describe("side panel width", () => {
  it("defaults to the design width", () => {
    expect(readReviewPanelWidth(VIEWPORT)).toBe(REVIEW_PANEL_DEFAULT_WIDTH)
  })

  it("never squeezes the PR content column", () => {
    expect(clampReviewPanelWidth(2000, VIEWPORT)).toBe(MAX_WIDTH)
  })

  it("never shrinks below the panel minimum", () => {
    expect(clampReviewPanelWidth(10, VIEWPORT)).toBe(REVIEW_PANEL_MIN_WIDTH)
  })

  it("re-clamps a stored width against a narrower viewport", () => {
    window.localStorage.setItem("open-swe.review-panel.width", "900")

    expect(readReviewPanelWidth(VIEWPORT)).toBe(MAX_WIDTH)
  })

  it("ignores a non-numeric stored width", () => {
    window.localStorage.setItem("open-swe.review-panel.width", "wide")

    expect(readReviewPanelWidth(VIEWPORT)).toBe(REVIEW_PANEL_DEFAULT_WIDTH)
  })

  it("persists a resize, clamped to the viewport", () => {
    const ref = { current: null }
    const { result } = renderHook(() => useReviewPanelWidth(ref))

    act(() => result.current[1](2000))

    expect(result.current[0]).toBe(MAX_WIDTH)
    expect(readReviewPanelWidth(VIEWPORT)).toBe(MAX_WIDTH)
  })
})
