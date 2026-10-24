import { describe, expect, it } from "vitest"

import { parseEvalOutput } from "./evalOutput"

describe("parseEvalOutput", () => {
  it("splits console output from an escaped result", () => {
    expect(
      parseEvalOutput(
        '<stdout>\nlog <b>\n</stdout>\n<result kind="handle">a &lt; b &amp;&amp; c</result>'
      )
    ).toEqual({ stdout: "log <b>", result: "a < b && c", error: null })
  })

  it("reads an error", () => {
    expect(
      parseEvalOutput('<error type="TypeError">x is &lt;undefined&gt;</error>')
    ).toEqual({
      stdout: null,
      result: null,
      error: { type: "TypeError", message: "x is <undefined>" },
    })
  })

  it("leaves other output alone", () => {
    expect(parseEvalOutput("plain text")).toBeNull()
  })
})
