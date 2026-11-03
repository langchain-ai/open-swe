import { beforeEach, describe, expect, it } from "vitest"

import type { ReviewDiffFile, ReviewWalkthrough } from "@/lib/api"
import { buildEntries, findEntry } from "./diffEntries"
import { findMatches, findTarget } from "./find"
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

describe("the page's lifetimes", () => {
  const pr = { owner: "acme", repo: "app", number: 7 }
  const page = () => useReviewPage.getState()

  beforeEach(() => {
    page().open({ owner: "acme", repo: "other", number: 1 }, null)
    page().open(pr, split.headSha)
    page().setEntryOrder(entries)
  })

  it("starts the visit over on coming back, keeping what was written and opened", () => {
    page().setActive(second, true)
    page().jumpTo({ kind: "entry", id: second.id })
    page().setComposerText("half a thought")
    page().setReplyDraft(42, "a reply")
    page().setExpandedFinding("finding-1")
    page().setThreadOpen(42, true)
    page().showFindings()
    useReviewPage.setState({ lastStop: "src/a.ts:RIGHT:7" })

    page().leave()
    page().open(pr, split.headSha)

    const state = page()
    expect(state.activeEntry).toBeNull()
    expect(state.atEnd).toBe(false)
    expect(state.visitedSteps.size).toBe(0)
    expect(state.jump).toBeNull()
    expect(state.lastStop).toBeNull()
    expect(state.entryOrder).toEqual([])
    // Asked for on the last visit; nothing on this one should act on it.
    expect(state.takeEvent("showFindings")).toBe(false)
    expect(state.composerText).toBe("half a thought")
    expect(state.replyDrafts.get(42)).toBe("a reply")
    expect(state.expandedFinding).toBe("finding-1")
    expect(state.threadsOpen.get(42)).toBe(true)
  })

  it("drops drafts on another pull request, and folds on a new head", () => {
    page().toggleCollapsed(first.readKey)
    page().open(pr, "c".repeat(40))
    expect(page().collapsed.size).toBe(0)

    page().setReplyDraft(42, "a reply")
    page().setThreadOpen(42, true)
    page().open({ ...pr, number: 8 }, null)
    expect(page().replyDrafts.size).toBe(0)
    expect(page().threadsOpen.size).toBe(0)
  })

  it("x folds the file being read and leaves it folded", () => {
    page().setActive(first, false)

    page().toggleActiveCollapsed()

    expect(page().jump?.target).toEqual({ kind: "entry", id: first.id })
    expect(isCollapsed(page(), first)).toBe(true)
    // Only a jump that asks to, like a click in the file list, opens it.
    page().jumpTo({ kind: "file", path: split.path }, { reveal: true })
    expect(isCollapsed(page(), first)).toBe(false)
  })

  it("marking the last unread file viewed stays put and leaves it folded", () => {
    page().setActive(first, false)
    page().markViewed(first.id)
    expect(page().jump?.target).toEqual({ kind: "entry", id: second.id })

    const jump = page().jump

    page().markViewed(second.id)

    expect(page().jump).toBe(jump)
    expect(isCollapsed(page(), second)).toBe(true)
  })
})

describe("finding in the changes", () => {
  it("finds a file by name and each changed line once, in the step that shows it", () => {
    const seven = findMatches(entries, "SEVEN")
    expect(seven).toEqual([
      { kind: "line", path: split.path, line: 7, side: "LEFT" },
      { kind: "line", path: split.path, line: 7, side: "RIGHT" },
    ])
    expect(findEntry(entries, findTarget(seven[0]!))).toBe(second)
    expect(findMatches(entries, "TWO")[0]).toMatchObject({ line: 2 })
    expect(
      findMatches(buildEntries([split], walkthrough, "files"), "src/a")
    ).toEqual([{ kind: "file", path: split.path }])
  })
})
