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
})
