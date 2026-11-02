import { describe, expect, it } from "vitest"

import { preserveFormatting } from "@/lib/preserve"

const original = [
  "# Handbook",
  "",
  "Welcome.",
  "",
  "| A | B |",
  "| --- | --- |",
  "| 1 | 2 |",
].join("\n")
// The editor pads tables; nobody edited this one.
const normalized = [
  "# Handbook",
  "",
  "Welcome.",
  "",
  "| A   | B   |",
  "| --- | --- |",
  "| 1   | 2   |",
].join("\n")

describe("preserveFormatting", () => {
  it("changes only the lines that were edited", () => {
    const current = normalized.replace("Welcome.", "Welcome, everyone.")
    expect(preserveFormatting(original, normalized, current)).toBe(
      original.replace("Welcome.", "Welcome, everyone.")
    )
  })

  it("takes the editor's version of just the line someone edited", () => {
    const current = normalized.replace("| 1   | 2   |", "| 1   | 3   |")
    expect(preserveFormatting(original, normalized, current)).toBe(
      original.replace("| 1 | 2 |", "| 1   | 3   |")
    )
  })

  it("keeps added paragraphs where they were written", () => {
    const current = normalized.replace("Welcome.", "Welcome.\n\nSay hi.")
    expect(preserveFormatting(original, normalized, current)).toBe(
      original.replace("Welcome.", "Welcome.\n\nSay hi.")
    )
  })
})
