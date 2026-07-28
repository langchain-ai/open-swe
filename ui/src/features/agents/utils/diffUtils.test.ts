/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  DIFF_FIXED_LINE_HEIGHT_CSS,
  buildDiffOptions,
  fileContentsCacheKey,
  readStoredDiffOverflow,
  writeStoredDiffOverflow,
} from "./diffUtils"

beforeEach(() => window.localStorage.clear())

describe("diff overflow preference", () => {
  it("defaults to horizontal scrolling", () => {
    expect(readStoredDiffOverflow()).toBe("scroll")
  })

  it("persists wrapping", () => {
    writeStoredDiffOverflow("wrap")

    expect(readStoredDiffOverflow()).toBe("wrap")
  })

  it("keeps fixed line heights only in scroll mode", () => {
    const scrollOptions = buildDiffOptions("unified", "scroll", "dark")
    const wrapOptions = buildDiffOptions("split", "wrap", "light")

    expect(scrollOptions.overflow).toBe("scroll")
    expect(scrollOptions.unsafeCSS).toContain(DIFF_FIXED_LINE_HEIGHT_CSS)
    expect(wrapOptions.overflow).toBe("wrap")
    expect(wrapOptions.diffStyle).toBe("split")
    expect(wrapOptions.unsafeCSS).not.toContain(DIFF_FIXED_LINE_HEIGHT_CSS)
  })
})

describe("fileContentsCacheKey", () => {
  it("builds a stable key from string contents", () => {
    const key = fileContentsCacheKey("src/a.ts", "new", "hello")
    expect(key.startsWith("src/a.ts:new:5:")).toBe(true)
    expect(fileContentsCacheKey("src/a.ts", "new", "hello")).toBe(key)
  })

  it("treats null contents as empty instead of crashing", () => {
    // Binary/oversized/added/removed blobs arrive as null from the backend; the
    // reviews page still computes this key in an unconditional useMemo.
    expect(() => fileContentsCacheKey("bin.png", "old", null)).not.toThrow()
    expect(fileContentsCacheKey("bin.png", "old", null)).toBe(
      fileContentsCacheKey("bin.png", "old", "")
    )
  })

  it("treats undefined contents as empty", () => {
    expect(() =>
      fileContentsCacheKey("bin.png", "new", undefined)
    ).not.toThrow()
    expect(fileContentsCacheKey("bin.png", "new", undefined)).toBe(
      fileContentsCacheKey("bin.png", "new", "")
    )
  })
})
