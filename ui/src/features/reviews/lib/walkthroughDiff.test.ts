import { describe, expect, it } from "vitest"

import { walkthroughFileDiff } from "@/features/reviews/lib/walkthroughDiff"
import type { ReviewDiffFile } from "@/lib/api"

function file(): ReviewDiffFile {
  return {
    baseSha: "a".repeat(40),
    headSha: "b".repeat(40),
    path: "src/a.ts",
    previousPath: null,
    status: "modified",
    additions: 2,
    deletions: 2,
    position: 0,
    patch:
      "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -3,1 +3,1 @@\n-line 3\n+line three\n@@ -35,1 +35,1 @@\n-line 35\n+line thirty-five\n",
  }
}

describe("walkthroughFileDiff", () => {
  it("keeps only the hunks holding the step's lines, at their real line numbers", () => {
    const diff = walkthroughFileDiff(file(), {
      path: "src/a.ts",
      added: [[35, 35]],
      deleted: [],
    })
    expect(diff?.hunks).toHaveLength(1)
    const hunk = diff?.hunks[0]
    expect(hunk?.additionStart).toBeLessThanOrEqual(35)
    expect(
      (hunk?.additionStart ?? 0) + (hunk?.additionCount ?? 0) - 1
    ).toBeGreaterThanOrEqual(35)
    expect(diff?.additionLines.join("")).toContain("line thirty-five")
    expect(diff?.additionLines.join("")).not.toContain("line three")
  })

  it("matches deleted lines against the original file's numbering", () => {
    const diff = walkthroughFileDiff(file(), {
      path: "src/a.ts",
      added: [],
      deleted: [[3, 3]],
    })
    expect(diff?.hunks).toHaveLength(1)
    expect(diff?.deletionLines.join("")).toContain("line 3\n")
  })

  it("renders the whole file when the step owns every hunk", () => {
    expect(
      walkthroughFileDiff(file(), {
        path: "src/a.ts",
        added: [
          [3, 3],
          [35, 35],
        ],
        deleted: [],
      })
    ).toBeNull()
  })
})
