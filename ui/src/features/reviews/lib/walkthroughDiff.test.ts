import { describe, expect, it } from "vitest"

import { walkthroughFileDiff } from "@/features/reviews/lib/walkthroughDiff"
import type { ReviewDiffFile } from "@/lib/api"

const lines = (count: number) =>
  Array.from({ length: count }, (_, i) => `line ${i + 1}\n`).join("")

function file(original: string, modified: string): ReviewDiffFile {
  return {
    path: "src/a.ts",
    previousPath: null,
    status: "modified",
    additions: 2,
    deletions: 2,
    originalContent: original,
    modifiedContent: modified,
  }
}

describe("walkthroughFileDiff", () => {
  const original = lines(40)
  const modified = original
    .replace("line 3\n", "line three\n")
    .replace("line 35\n", "line thirty-five\n")

  it("keeps only the hunks holding the step's lines, at their real line numbers", () => {
    const diff = walkthroughFileDiff(file(original, modified), {
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
    const diff = walkthroughFileDiff(file(original, modified), {
      path: "src/a.ts",
      added: [],
      deleted: [[3, 3]],
    })
    expect(diff?.hunks).toHaveLength(1)
    expect(diff?.deletionLines.join("")).toContain("line 3\n")
  })

  it("renders the whole file when the step owns every hunk", () => {
    expect(
      walkthroughFileDiff(file(original, modified), {
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
