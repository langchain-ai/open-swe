import { describe, expect, it } from "vitest"

import { parseQuotes, serializeQuotes } from "./transcriptQuote"

describe("transcript quotes", () => {
  it("round-trips multi-line quotes ahead of the message", () => {
    const quotes = ["first line\n\nsame quote, after a blank line", "> nested"]
    const content = serializeQuotes("Why?", quotes)

    expect(parseQuotes(content)).toEqual({ quotes, text: "Why?" })
  })

  it("leaves a quote typed mid-message in the text", () => {
    const content = "See this:\n> not a leading quote"

    expect(parseQuotes(content)).toEqual({ quotes: [], text: content })
  })
})
