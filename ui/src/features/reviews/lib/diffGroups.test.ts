import { describe, expect, it } from "vitest"

import type { ReviewDiffFile } from "./api"
import { resolveDiffGroups, stripLocationLinks } from "./diffGroups"

function file(path: string, additions = 1, deletions = 0): ReviewDiffFile {
  return {
    path,
    previousPath: null,
    status: "modified",
    additions,
    deletions,
    originalContent: "",
    modifiedContent: "",
  }
}

function group(title: string, files: Array<string>, index = 1) {
  return { index, title, summary: `${title} summary`, files }
}

describe("resolveDiffGroups", () => {
  it("returns null while the diff is still loading", () => {
    expect(resolveDiffGroups(null, [group("A", ["a.ts"])], false)).toBeNull()
  })

  it("returns null when the groups are stale", () => {
    expect(
      resolveDiffGroups([file("a.ts")], [group("A", ["a.ts"])], true)
    ).toBeNull()
  })

  it("returns null when there are no groups", () => {
    expect(resolveDiffGroups([file("a.ts")], [], false)).toBeNull()
  })

  it("returns null when no group matches and there is nothing left over", () => {
    expect(resolveDiffGroups([], [group("A", ["a.ts"])], false)).toBeNull()
  })

  it("keeps the group order, renumbers from 1, and sums each group's stats", () => {
    const resolved = resolveDiffGroups(
      [file("a.ts", 3, 1), file("b.ts", 4, 2)],
      [group("A", ["a.ts"], 7), group("B", ["b.ts"], 9)],
      false
    )
    expect(resolved).toEqual([
      {
        index: 1,
        title: "A",
        summary: "A summary",
        files: [file("a.ts", 3, 1)],
        additions: 3,
        deletions: 1,
      },
      {
        index: 2,
        title: "B",
        summary: "B summary",
        files: [file("b.ts", 4, 2)],
        additions: 4,
        deletions: 2,
      },
    ])
  })

  it("drops paths that are no longer in the diff, and groups left empty by that", () => {
    const resolved = resolveDiffGroups(
      [file("a.ts")],
      [group("A", ["a.ts", "gone.ts"]), group("B", ["also-gone.ts"])],
      false
    )
    expect(resolved).toEqual([
      {
        index: 1,
        title: "A",
        summary: "A summary",
        files: [file("a.ts")],
        additions: 1,
        deletions: 0,
      },
    ])
  })

  it("gives a file claimed by two groups to the first one", () => {
    const resolved = resolveDiffGroups(
      [file("a.ts"), file("b.ts")],
      [group("A", ["a.ts"]), group("B", ["a.ts", "b.ts"])],
      false
    )
    expect(resolved?.map((g) => g.files.map((f) => f.path))).toEqual([
      ["a.ts"],
      ["b.ts"],
    ])
  })

  it("sweeps unassigned files into a trailing 'Other changes' group", () => {
    const resolved = resolveDiffGroups(
      [file("a.ts"), file("b.ts", 5, 6), file("c.ts", 1, 2)],
      [group("A", ["a.ts"])],
      false
    )
    expect(resolved?.[1]).toEqual({
      index: 2,
      title: "Other changes",
      summary: "",
      files: [file("b.ts", 5, 6), file("c.ts", 1, 2)],
      additions: 6,
      deletions: 8,
    })
  })
})

describe("stripLocationLinks", () => {
  it("rewrites a #loc link as inline code", () => {
    expect(
      stripLocationLinks("see [agent/foo.py:12](#loc=agent/foo.py:12).")
    ).toBe("see `agent/foo.py:12`.")
  })

  it("rewrites every #loc link in the summary", () => {
    expect(stripLocationLinks("[a](#loc=a) and [b](#loc=b:3)")).toBe(
      "`a` and `b`"
    )
  })

  it("leaves ordinary links alone", () => {
    expect(stripLocationLinks("[docs](https://example.com)")).toBe(
      "[docs](https://example.com)"
    )
  })
})
