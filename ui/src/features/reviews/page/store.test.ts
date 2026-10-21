import { beforeEach, describe, expect, it } from "vitest"

import type { ReviewDiffFile, ReviewWalkthrough } from "@/lib/api"
import { buildEntries } from "./diffEntries"
import { isCollapsed, isRead, useReviewPage } from "./store"

// One file whose two edits the walkthrough splits between steps 1 and 2.
const split: ReviewDiffFile = {
  baseSha: "a".repeat(40),
  headSha: "b".repeat(40),
  path: "src/a.ts",
  previousPath: null,
  status: "modified",
  additions: 2,
  deletions: 2,
  patch: [
    "diff --git a/src/a.ts b/src/a.ts",
    "--- a/src/a.ts",
    "+++ b/src/a.ts",
    "@@ -1,9 +1,9 @@",
    " one",
    "-two",
    "+TWO",
    " three",
    " four",
    " five",
    " six",
    "-seven",
    "+SEVEN",
    " eight",
    " nine",
    "",
  ].join("\n"),
}

const walkthrough: ReviewWalkthrough = {
  head_sha: split.headSha,
  human_input: "",
  steps: [
    {
      index: 1,
      title: "First edit",
      summary: "",
      other: false,
      files: [{ path: split.path, added: [[2, 2]], deleted: [[2, 2]] }],
    },
    {
      index: 2,
      title: "Second edit",
      summary: "",
      other: false,
      files: [{ path: split.path, added: [[7, 7]], deleted: [[7, 7]] }],
    },
  ],
}

const entries = buildEntries([split], walkthrough, "guide")
const [first, second] = entries as [
  (typeof entries)[number],
  (typeof entries)[number],
]

describe("reading a walkthrough", () => {
  beforeEach(() => {
    useReviewPage.setState({
      pr: null,
      viewed: new Set(),
      collapsed: new Set(),
      entryOrder: entries,
      activeEntry: null,
    })
  })

  it("reads a step without touching the other step's part of the same file", () => {
    useReviewPage.getState().markStepRead(1)

    const state = useReviewPage.getState()
    expect(isRead(state.viewed, first)).toBe(true)
    expect(isCollapsed(state, first)).toBe(true)
    expect(isRead(state.viewed, second)).toBe(false)
    expect(isCollapsed(state, second)).toBe(false)
    // The file counts as viewed only once every part of it is.
    expect(state.viewed.has(split.path)).toBe(false)

    useReviewPage.getState().markStepRead(2)
    expect(useReviewPage.getState().viewed.has(split.path)).toBe(true)
  })

  it("unreading one part of a viewed file keeps the rest read", () => {
    useReviewPage.getState().markStepRead(1)
    useReviewPage.getState().markStepRead(2)

    useReviewPage.getState().markStepRead(1)

    const { viewed } = useReviewPage.getState()
    expect(viewed.has(split.path)).toBe(false)
    expect(isRead(viewed, first)).toBe(false)
    expect(isRead(viewed, second)).toBe(true)
  })
})
