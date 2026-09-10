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

  // A deleted line whose content starts `-- ` renders as `--- foo`, and an
  // added one as `+++ foo` — byte-identical to a file header pair.
  it("keeps deleted lines that look like file headers inside their hunk", () => {
    const patch = [
      "--- a/opts.txt",
      "+++ b/opts.txt",
      "@@ -1,3 +1,3 @@",
      " keep",
      "--- option",
      "+++ added",
      " tail",
    ].join("\n")
    const files = splitPatch(patch)
    expect(files).toHaveLength(1)
    expect(files[0]?.path).toBe("opts.txt")
    expect(files[0]?.patch).toContain("--- option")
    expect(selectPatchLines(files[0]!.patch, "deletions", 1, 2)).toEqual([
      " keep",
      "--- option",
    ])
  })

  it("still splits a real second file that follows a completed hunk", () => {
    const patch = [
      "--- a/x.txt",
      "+++ b/x.txt",
      "@@ -1,2 +1,1 @@",
      " keep",
      "--- option",
      "--- a/y.txt",
      "+++ b/y.txt",
      "@@ -1 +1 @@",
      "-old",
      "+new",
    ].join("\n")
    expect(splitPatch(patch).map((file) => file.path)).toEqual([
      "x.txt",
      "y.txt",
    ])
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
