/** @vitest-environment jsdom */

import { afterEach, describe, expect, it } from "vitest"

import type { ReviewDiffFile } from "./api"
import {
  buildCommentPayload,
  buildSelectionAttachments,
  commentRangeLabel,
  lineMetaFromNode,
  makeSideAttachment,
  readDiffSelection,
  selectedRangeFromDiff,
} from "./diffSelection"

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

function diffFile(path = "src/app.ts"): ReviewDiffFile {
  return {
    path,
    previousPath: null,
    status: "modified",
    additions: 2,
    deletions: 2,
    originalContent: "old1\nold2\nold3\nold4",
    modifiedContent: "new1\nnew2\nnew3\nnew4",
  }
}

describe("makeSideAttachment", () => {
  it("slices the modified file for an addition range", () => {
    const attachment = makeSideAttachment(diffFile(), "additions", 2, 3)
    expect(attachment.id).toMatch(UUID)
    expect(attachment.path).toBe("src/app.ts")
    expect(attachment.lineLabel).toBe("R2-3")
    expect(attachment.language).toBe("ts")
    expect(attachment.snippet).toBe("new2\nnew3")
  })

  it("slices the original file for a deletion range", () => {
    const attachment = makeSideAttachment(diffFile(), "deletions", 1, 1)
    expect(attachment.lineLabel).toBe("L1")
    expect(attachment.snippet).toBe("old1")
  })

  it("normalises a range given back to front", () => {
    const attachment = makeSideAttachment(diffFile(), "additions", 4, 2)
    expect(attachment.lineLabel).toBe("R2-4")
    expect(attachment.snippet).toBe("new2\nnew3\nnew4")
  })

  it("clamps a start line below the first line", () => {
    const attachment = makeSideAttachment(diffFile(), "additions", 0, 2)
    expect(attachment.lineLabel).toBe("R1-2")
    expect(attachment.snippet).toBe("new1\nnew2")
  })

  it("reports no language for an extensionless path", () => {
    expect(
      makeSideAttachment(diffFile("Makefile"), "additions", 1, 1).language
    ).toBe("")
  })
})

describe("buildSelectionAttachments", () => {
  it("makes one attachment for a same-side range", () => {
    const attachments = buildSelectionAttachments(diffFile(), {
      start: 2,
      end: 3,
      side: "additions",
      endSide: "additions",
    })
    expect(attachments.map((a) => [a.lineLabel, a.snippet])).toEqual([
      ["R2-3", "new2\nnew3"],
    ])
  })

  it("defaults a range with no side to the additions side", () => {
    const attachments = buildSelectionAttachments(diffFile(), {
      start: 1,
      end: 1,
    })
    expect(attachments.map((a) => [a.lineLabel, a.snippet])).toEqual([
      ["R1", "new1"],
    ])
  })

  it("splits a cross-side range into one line per side", () => {
    const attachments = buildSelectionAttachments(diffFile(), {
      start: 2,
      end: 4,
      side: "deletions",
      endSide: "additions",
    })
    expect(attachments.map((a) => [a.lineLabel, a.snippet])).toEqual([
      ["L2", "old2"],
      ["R4", "new4"],
    ])
  })

  it("splits a cross-side range dragged upwards the same way", () => {
    const attachments = buildSelectionAttachments(diffFile(), {
      start: 4,
      end: 2,
      side: "additions",
      endSide: "deletions",
    })
    expect(attachments.map((a) => [a.lineLabel, a.snippet])).toEqual([
      ["L2", "old2"],
      ["R4", "new4"],
    ])
  })
})

describe("lineMetaFromNode", () => {
  function render(html: string) {
    document.body.innerHTML = html
  }

  it("reads the line number and side from the nearest line element", () => {
    render(
      `<div data-line="12" data-line-type="deletion"><span id="t">x</span></div>`
    )
    expect(lineMetaFromNode(document.getElementById("t")!.firstChild)).toEqual({
      line: 12,
      side: "deletions",
    })
  })

  it("treats any non-deletion line type as the additions side", () => {
    render(`<div id="l" data-line="3" data-line-type="addition">x</div>`)
    expect(lineMetaFromNode(document.getElementById("l"))).toEqual({
      line: 3,
      side: "additions",
    })
  })

  it("returns null outside a line element", () => {
    render(`<div id="l">x</div>`)
    expect(lineMetaFromNode(document.getElementById("l"))).toBeNull()
  })

  it("returns null for a non-numeric line number", () => {
    render(`<div id="l" data-line="oops">x</div>`)
    expect(lineMetaFromNode(document.getElementById("l"))).toBeNull()
  })

  it("returns null for no node at all", () => {
    expect(lineMetaFromNode(null)).toBeNull()
  })
})

describe("readDiffSelection", () => {
  it("prefers the container's scoped shadow-root selection", () => {
    const scoped = { rangeCount: 0 } as unknown as Selection
    const container = {
      shadowRoot: { getSelection: () => scoped },
    } as unknown as Element
    expect(readDiffSelection(container)).toBe(scoped)
  })

  it("falls back to the document selection", () => {
    expect(readDiffSelection(null)).toBe(document.getSelection())
  })
})

describe("selectedRangeFromDiff", () => {
  afterEach(() => {
    document.getSelection()?.removeAllRanges()
    document.body.innerHTML = ""
  })

  function select(startId: string, endId: string) {
    const range = document.createRange()
    range.setStart(document.getElementById(startId)!.firstChild!, 0)
    range.setEnd(document.getElementById(endId)!.firstChild!, 1)
    const selection = document.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
  }

  it("maps a highlight spanning both sides to a cross-side range", () => {
    document.body.innerHTML = `
      <div data-line="4" data-line-type="deletion"><span id="a">old</span></div>
      <div data-line="9" data-line-type="addition"><span id="b">new</span></div>
    `
    select("a", "b")
    expect(selectedRangeFromDiff(null)).toEqual({
      start: 4,
      side: "deletions",
      end: 9,
      endSide: "additions",
    })
  })

  it("returns null for a collapsed selection", () => {
    document.body.innerHTML = `<div data-line="4"><span id="a">old</span></div>`
    const range = document.createRange()
    range.setStart(document.getElementById("a")!.firstChild!, 1)
    range.collapse(true)
    const selection = document.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    expect(selectedRangeFromDiff(null)).toBeNull()
  })

  it("returns null when the highlight is outside any diff line", () => {
    document.body.innerHTML = `<p id="a">plain</p><p id="b">text</p>`
    select("a", "b")
    expect(selectedRangeFromDiff(null)).toBeNull()
  })
})

describe("buildCommentPayload", () => {
  it("keeps a same-side multi-line range as a start/end pair", () => {
    expect(
      buildCommentPayload(
        "src/app.ts",
        { start: 10, end: 14, side: "additions", endSide: "additions" },
        "looks off"
      )
    ).toEqual({
      path: "src/app.ts",
      line: 14,
      side: "RIGHT",
      body: "looks off",
      start_line: 10,
      start_side: "RIGHT",
    })
  })

  it("orders a range dragged upwards", () => {
    expect(
      buildCommentPayload(
        "src/app.ts",
        { start: 14, end: 10, side: "deletions", endSide: "deletions" },
        "hm"
      )
    ).toEqual({
      path: "src/app.ts",
      line: 14,
      side: "LEFT",
      body: "hm",
      start_line: 10,
      start_side: "LEFT",
    })
  })

  it("drops start_line for a single line", () => {
    expect(
      buildCommentPayload("src/app.ts", { start: 7, end: 7 }, "nit")
    ).toEqual({
      path: "src/app.ts",
      line: 7,
      side: "RIGHT",
      body: "nit",
      start_line: null,
      start_side: null,
    })
  })

  it("collapses a cross-side range onto the end side", () => {
    expect(
      buildCommentPayload(
        "src/app.ts",
        { start: 3, end: 8, side: "deletions", endSide: "additions" },
        "why"
      )
    ).toEqual({
      path: "src/app.ts",
      line: 8,
      side: "RIGHT",
      body: "why",
      start_line: null,
      start_side: null,
    })
  })
})

describe("commentRangeLabel", () => {
  it("labels a single line by its end side", () => {
    expect(commentRangeLabel({ start: 7, end: 7, side: "deletions" })).toBe(
      "L7"
    )
  })

  it("labels a range low to high", () => {
    expect(commentRangeLabel({ start: 12, end: 9, side: "additions" })).toBe(
      "R9-12"
    )
  })

  it("prefers endSide over side", () => {
    expect(
      commentRangeLabel({
        start: 2,
        end: 2,
        side: "additions",
        endSide: "deletions",
      })
    ).toBe("L2")
  })
})
