import { describe, expect, it } from "vitest"

import { getSingularPatch } from "@pierre/diffs"

import {
  canHydrateDiffs,
  walkthroughFileDiff,
} from "@/features/reviews/lib/walkthroughDiff"
import type { ReviewDiffFile } from "@/lib/api"

// One hunk holding two edits git merged because they sit a few lines apart.
function file(): ReviewDiffFile {
  return {
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
}

describe("walkthroughFileDiff", () => {
  it("shows each step only its own part of a hunk the plan split, at real line numbers", () => {
    const first = walkthroughFileDiff(file(), {
      path: "src/a.ts",
      added: [[2, 2]],
      deleted: [[2, 2]],
    })
    const second = walkthroughFileDiff(file(), {
      path: "src/a.ts",
      added: [[7, 7]],
      deleted: [[7, 7]],
    })

    expect(first?.hunks.map((h) => [h.additionStart, h.additionCount])).toEqual(
      [[1, 5]]
    )
    expect(first?.additionLines.join("")).toContain("TWO")
    expect(first?.additionLines.join("")).not.toContain("SEVEN")
    expect(
      second?.hunks.map((h) => [h.additionStart, h.additionCount])
    ).toEqual([[4, 6]])
    expect(second?.additionLines.join("")).toContain("SEVEN")
    expect(second?.additionLines.join("")).not.toContain("TWO")
  })

  it("disables full-file hydration for a view containing a slice with omitted deletions", () => {
    const changed = file()
    changed.deletions = 3
    changed.patch = changed
      .patch!.replace("@@ -1,9 +1,9 @@", "@@ -1,9 +1,8 @@")
      .replace("-seven\n+SEVEN\n eight", "-seven\n-eight\n+SEVEN")
    const whole = getSingularPatch(changed.patch)
    const slice = walkthroughFileDiff(changed, {
      path: changed.path,
      added: [[2, 2]],
      deleted: [[2, 2]],
    })!

    expect(slice.additionLines.join("")).not.toContain("SEVEN")
    expect(slice.isPartial).toBe(true)
    expect(canHydrateDiffs([whole])).toBe(true)
    expect(canHydrateDiffs([whole, slice])).toBe(false)
    expect(
      canHydrateDiffs([
        walkthroughFileDiff(changed, {
          path: changed.path,
          added: [[2, 2]],
          deleted: [[2, 2]],
        })!,
      ])
    ).toBe(false)
    expect(canHydrateDiffs([whole])).toBe(true)
  })

  it("renders the whole file when the step holds every changed line", () => {
    expect(
      walkthroughFileDiff(file(), {
        path: "src/a.ts",
        added: [
          [2, 2],
          [7, 7],
        ],
        deleted: [
          [2, 2],
          [7, 7],
        ],
      })
    ).toBeNull()
  })
})
