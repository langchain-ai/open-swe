import { describe, expect, test } from "bun:test"

import { composePrompt } from "../src/input.ts"

describe("composePrompt", () => {
  test("attaches piped input below the instruction", () => {
    expect(composePrompt("review this diff", "diff --git a/x b/x\n+y\n")).toBe(
      "review this diff\n\n<stdin>\ndiff --git a/x b/x\n+y\n</stdin>"
    )
  })

  test("uses whichever one is present on its own", () => {
    expect(composePrompt("  what changed?  ", "")).toBe("what changed?")
    expect(composePrompt("", "the whole prompt\n")).toBe("the whole prompt")
  })

  test("ignores piped input that is only whitespace", () => {
    expect(composePrompt("hi", "\n \n")).toBe("hi")
  })
})
