import { describe, expect, it } from "vitest"

import { passageAround } from "./passageReply"

describe("passageAround", () => {
  const text = "one\ntwo\nthree four five\nsix\nseven"

  it("keeps a line of context on each side of a mid-line selection", () => {
    const start = text.indexOf("four")

    expect(passageAround(text, start, start + "four".length)).toEqual({
      before: "two\nthree ",
      selection: "four",
      after: " five\nsix",
    })
  })

  it("moves edge whitespace into the context and stops at the text's ends", () => {
    expect(passageAround(text, 0, "one\n".length)).toEqual({
      before: "",
      selection: "one",
      after: "\ntwo",
    })
    const last = text.indexOf("seven")
    expect(passageAround(text, last - 1, text.length)).toEqual({
      before: "six\n",
      selection: "seven",
      after: "",
    })
  })

  it("trims long context to the part nearest the selection", () => {
    const long = `${"a".repeat(5_000)}\nmid\n${"b".repeat(5_000)}`
    const start = long.indexOf("mid")

    const passage = passageAround(long, start, start + 3)
    expect(passage.selection).toBe("mid")
    expect(passage.before).toBe(`${"a".repeat(999)}\n`)
    expect(passage.after).toBe(`\n${"b".repeat(999)}`)
  })
})
