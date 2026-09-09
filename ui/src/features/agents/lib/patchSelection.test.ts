import { describe, expect, it } from "vitest"

import { selectPatchLines, splitPatch } from "./patchSelection"

const GIT_PATCH = [
  "diff --git a/src/a.py b/src/a.py",
  "index 1..2 100644",
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,3 +1,4 @@",
  " one",
  "-two",
  "+TWO",
  "+two-and-a-half",
  " three",
  "diff --git a/src/b.py b/src/b.py",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/src/b.py",
  "@@ -0,0 +1,2 @@",
  "+alpha",
  "+beta",
  "",
].join("\n")

describe("splitPatch", () => {
  it("splits a git patch into per-file patches with their new paths", () => {
    const files = splitPatch(GIT_PATCH)
    expect(files.map((file) => file.path)).toEqual(["src/a.py", "src/b.py"])
    expect(files[0]?.patch.startsWith("diff --git a/src/a.py")).toBe(true)
    expect(files[1]?.patch).toContain("+beta")
    expect(files[1]?.patch).not.toContain("+TWO")
  })

  it("splits plain unified diffs on the next file header", () => {
    const patch = [
      "--- a/x.txt",
      "+++ b/x.txt",
      "@@ -1 +1 @@",
      "-old",
      "+new",
      "--- a/y.txt",
      "+++ b/y.txt",
      "@@ -1 +1 @@",
      "-foo",
      "+bar",
    ].join("\n")
    expect(splitPatch(patch).map((file) => file.path)).toEqual([
      "x.txt",
      "y.txt",
    ])
  })
})

describe("selectPatchLines", () => {
  const [a] = splitPatch(GIT_PATCH)

  it("returns added and context lines by new-file line number", () => {
    expect(selectPatchLines(a!.patch, "additions", 2, 4)).toEqual([
      "+TWO",
      "+two-and-a-half",
      " three",
    ])
  })

  it("returns removed and context lines by old-file line number", () => {
    expect(selectPatchLines(a!.patch, "deletions", 1, 2)).toEqual([
      " one",
      "-two",
    ])
  })
})
