import { describe, expect, it } from "vitest"

import { fileContentsCacheKey } from "./diffUtils"

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
