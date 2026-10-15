import { describe, expect, it } from "vitest"

import { parseEvalOutput } from "./evalOutput"

describe("parseEvalOutput", () => {
  it("splits console output from an escaped result", () => {
    expect(
      parseEvalOutput(
        '<stdout>\n{"a":1}\n</stdout>\n<result kind="string">a &lt;b&gt; &amp;amp;</result>'
      )
    ).toEqual({
      stdout: '{"a":1}',
      result: { kind: "string", value: "a <b> &amp;" },
      error: null,
    })
  })

  it("reads errors and rejects text that is not the eval format", () => {
    expect(
      parseEvalOutput('<error type="TypeError">x is &lt;undefined&gt;</error>')
    ).toEqual({
      stdout: null,
      result: null,
      error: { type: "TypeError", message: "x is <undefined>" },
    })
    expect(parseEvalOutput("plain output")).toBeNull()
  })
})
