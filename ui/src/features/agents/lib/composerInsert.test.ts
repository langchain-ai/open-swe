import { describe, expect, it } from "vitest"

import {
  insertIntoComposer,
  quoteFileLines,
  subscribeComposerInsert,
} from "./composerInsert"

describe("composerInsert", () => {
  it("delivers text to subscribers and reports whether any listened", () => {
    expect(insertIntoComposer("nobody")).toBe(false)
    const received: Array<string> = []
    const unsubscribe = subscribeComposerInsert((text) => received.push(text))
    expect(insertIntoComposer("hello")).toBe(true)
    unsubscribe()
    insertIntoComposer("gone")
    expect(received).toEqual(["hello"])
  })

  it("quotes a file excerpt as a fenced block with its location", () => {
    expect(quoteFileLines("src/a.py", 2, 3, ["b", "c"], "py")).toBe(
      "> `src/a.py:2-3`\n> ```py\n> b\n> c\n> ```\n\n"
    )
    expect(quoteFileLines("x", 7, 7, ["only"])).toBe(
      "> `x:7`\n> ```\n> only\n> ```\n\n"
    )
  })
})
