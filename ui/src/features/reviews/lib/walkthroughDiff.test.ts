import { describe, expect, it } from "vitest"

import {
  walkthroughFileDiff,
  walkthroughHunks,
} from "@/features/reviews/lib/walkthroughDiff"
import { buildEntries } from "@/features/reviews/page/diffEntries"
import type { ReviewDiffFile, ReviewWalkthroughStep } from "@/lib/api"

function file(): ReviewDiffFile {
  return {
    baseSha: "a".repeat(40),
    headSha: "b".repeat(40),
    path: "src/a.ts",
    previousPath: null,
    status: "modified",
    additions: 2,
    deletions: 2,
    patch:
      "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -3,1 +3,1 @@\n-line 3\n+line three\n@@ -35,1 +35,1 @@\n-line 35\n+line thirty-five\n",
  }
}

function step(
  index: number,
  added: Array<[number, number]>,
  other = false
): ReviewWalkthroughStep {
  return {
    index,
    title: `Step ${index}`,
    summary: "",
    other,
    files: [{ path: "src/a.ts", added, deleted: [] }],
  }
}

describe("walkthroughFileDiff", () => {
  it("keeps only the step's hunks, at their real line numbers", () => {
    const a = file()
    const held = walkthroughHunks(a, {
      path: "src/a.ts",
      added: [],
      deleted: [[35, 35]],
    })
    expect(held).toEqual([1])
    const diff = walkthroughFileDiff(a, held ?? [])
    expect(diff?.hunks).toHaveLength(1)
    expect(diff?.hunks[0]?.additionStart).toBe(35)
    expect(diff?.additionLines.join("")).toContain("line thirty-five")
    expect(diff?.additionLines.join("")).not.toContain("line three")
  })
})

describe("buildEntries", () => {
  it("shows each hunk once, in the first step that holds it", () => {
    const entries = buildEntries(
      [file()],
      {
        head_sha: "b".repeat(40),
        human_input: "",
        steps: [
          step(1, [[35, 35]]),
          step(2, [[3, 3]]),
          step(3, [[35, 35]]),
          step(4, [[3, 3]], true),
        ],
      },
      "guide"
    )
    expect(
      entries.map((entry) => [
        entry.step?.title,
        entry.fileDiff.hunks.map((hunk) => hunk.additionStart),
      ])
    ).toEqual([
      ["Step 1", [35]],
      ["Step 2", [3]],
    ])
  })
})
