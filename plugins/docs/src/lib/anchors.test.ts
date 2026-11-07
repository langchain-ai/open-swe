import { getSchema } from "@tiptap/core"
import StarterKit from "@tiptap/starter-kit"
import { expect, it } from "vitest"

import { resolveAnchor } from "@/lib/anchors"

const schema = getSchema([StarterKit])
const doc = schema.node(
  "doc",
  null,
  ["Set the limit to 10.", "Set the timeout to 10.", "Same.", "Same."].map(
    (text) => schema.node("paragraph", null, [schema.text(text)])
  )
)
const paragraphOf = (quote: string, prefix: string, suffix: string) => {
  const range = resolveAnchor(doc, { quote, prefix, suffix })
  return range && doc.resolve(range.from).index(0)
}

it("finds the passage its context points at, or the first with no context", () => {
  expect(paragraphOf("10", "the timeout to ", ".")).toBe(1)
  expect(paragraphOf("10", "", "")).toBe(0)
})

it("orphans a comment whose context no longer singles out its passage", () => {
  expect(paragraphOf("10", "a deadline of ", "!")).toBeNull()
  expect(paragraphOf("Same", "\n", ".")).toBeNull()
})
