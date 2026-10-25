import { describe, expect, it } from "vitest"

import { parseMessageSegments, serializeReplies } from "./quoteReply"

describe("quote replies", () => {
  it("reads each reply back as its passage, then the comment on it", () => {
    const content = serializeReplies("And overall?", [
      {
        kind: "text",
        quote: "first line\n\nsame quote, after a blank line",
        comment: "Why?",
      },
      {
        kind: "code",
        excerpt: {
          path: "src/app.ts",
          lineLabel: "R2",
          language: "ts",
          snippet: "  1 | a()\n> 2 | b()\n\n  3 | c()",
        },
        comment: "Rename this.",
      },
      { kind: "text", quote: "> nested", comment: "" },
    ])

    expect(parseMessageSegments(content)).toEqual([
      { kind: "quote", text: "first line\n\nsame quote, after a blank line" },
      { kind: "text", text: "Why?" },
      {
        kind: "code",
        excerpt: { location: "src/app.ts:R2", language: "ts", code: "b()" },
      },
      { kind: "text", text: "Rename this." },
      { kind: "quote", text: "> nested" },
      { kind: "text", text: "And overall?" },
    ])
  })

  it("keeps a quote marker inside a paragraph as prose", () => {
    const content = "See this:\n> not a quote\n\n\n\nstill prose"

    expect(parseMessageSegments(content)).toEqual([
      { kind: "text", text: content },
    ])
  })
})
