import { describe, expect, it } from "vitest"

import { collapsedUserTextSegments } from "./collapsedUserTextSegments"

describe("collapsedUserTextSegments", () => {
  it("leaves short text unchanged", () => {
    expect(collapsedUserTextSegments("small prompt")).toEqual([
      { type: "text", text: "small prompt" },
    ])
  })

  it("collapses long single-block text", () => {
    const text = "x".repeat(1_250)

    expect(collapsedUserTextSegments(text)).toEqual([
      { type: "pasted", text, label: "[Pasted 1.3k chars]" },
    ])
  })

  it("preserves a short prompt prefix before collapsed text", () => {
    const pasted = Array.from(
      { length: 25 },
      (_, index) => `line ${index}`
    ).join("\n")
    const text = `Please inspect this:\n${pasted}`

    expect(collapsedUserTextSegments(text)).toEqual([
      { type: "text", text: "Please inspect this:\n" },
      { type: "pasted", text: pasted, label: "[Pasted 25 lines]" },
    ])
  })
})
