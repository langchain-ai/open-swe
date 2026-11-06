import { describe, expect, it } from "vitest"

import { walkthroughFileDiff } from "@/features/reviews/lib/walkthroughDiff"
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

  it("reads a step's scattered lines of an added file with the neighbouring lines around them", () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`)
    const added: ReviewDiffFile = {
      ...file(),
      path: "src/new.ts",
      status: "added",
      additions: 30,
      deletions: 0,
      patch: [
        "diff --git a/src/new.ts b/src/new.ts",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/src/new.ts",
        "@@ -0,0 +1,30 @@",
        ...lines.map((line) => `+${line}`),
        "",
      ].join("\n"),
    }

    const slice = walkthroughFileDiff(added, {
      path: "src/new.ts",
      added: [
        [5, 5],
        [9, 9],
        [25, 25],
      ],
      deleted: [],
    })

    // The first two share one run; the third stands apart, each with three lines around it.
    expect(slice?.hunks.map((h) => [h.additionStart, h.additionCount])).toEqual(
      [
        [2, 11],
        [22, 7],
      ]
    )
    expect(slice?.hunks.map((h) => h.hunkContent.map((c) => c.type))).toEqual([
      ["context", "change", "context", "change", "context"],
      ["context", "change", "context"],
    ])
  })

  it("keeps a step's deletion on its real base line after a line another step added", () => {
    const modified: ReviewDiffFile = {
      ...file(),
      patch: [
        "diff --git a/src/a.ts b/src/a.ts",
        "--- a/src/a.ts",
        "+++ b/src/a.ts",
        "@@ -3,5 +3,6 @@",
        " c",
        " d",
        "+A",
        " e",
        "-X",
        "+Y",
        " g",
        "",
      ].join("\n"),
    }

    const slice = walkthroughFileDiff(modified, {
      path: "src/a.ts",
      added: [[7, 7]],
      deleted: [[6, 6]],
    })

    // A stays an added line, so X is base line 6: deletion lines d, e, X, g start at line 4.
    expect(slice?.hunks.map((h) => [h.deletionStart, h.additionStart])).toEqual(
      [[4, 4]]
    )
    expect(slice?.deletionLines).toEqual(["d\n", "e\n", "X\n", "g\n"])
  })

  it("keeps a step's own lines when other steps added lines above them at the top of the file", () => {
    const top: ReviewDiffFile = {
      ...file(),
      patch: [
        "diff --git a/src/a.ts b/src/a.ts",
        "--- a/src/a.ts",
        "+++ b/src/a.ts",
        "@@ -1,3 +1,5 @@",
        "+A",
        "+B",
        " a",
        "-b",
        "+Y",
        " c",
        "",
      ].join("\n"),
    }

    const slice = walkthroughFileDiff(top, {
      path: "src/a.ts",
      added: [[4, 4]],
      deleted: [[2, 2]],
    })

    expect(slice?.hunks.map((h) => [h.deletionStart, h.additionStart])).toEqual(
      [[1, 1]]
    )
    expect(slice?.deletionLines).toEqual(["a\n", "b\n", "c\n"])
  })
})
