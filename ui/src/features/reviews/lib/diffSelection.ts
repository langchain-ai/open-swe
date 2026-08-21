/**
 * Turning a selection inside a rendered diff into something actionable: a chat
 * attachment (a highlight the reader wants to ask about) or a GitHub inline
 * comment payload (a gutter range they want to comment on).
 */

import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"
import type {
  ReviewCommentCreate,
  ReviewDiffFile,
} from "@/features/reviews/lib/api"
import type { ChatAttachment } from "@/features/reviews/components/ReviewChat"

/**
 * One attachment for a single-side line range. Deletions resolve against the
 * original file, additions against the modified file.
 */
export function makeSideAttachment(
  file: ReviewDiffFile,
  side: SelectionSide,
  fromLine: number,
  toLine: number
): ChatAttachment {
  const source =
    side === "deletions" ? file.originalContent : file.modifiedContent
  const lines = source.split("\n")
  const start = Math.max(1, Math.min(fromLine, toLine))
  const end = Math.max(fromLine, toLine)
  const snippet = lines.slice(start - 1, end).join("\n")
  const sideLabel = side === "deletions" ? "L" : "R"
  const lineLabel =
    start === end ? `${sideLabel}${start}` : `${sideLabel}${start}-${end}`
  const language = file.path.includes(".")
    ? (file.path.split(".").pop() ?? "")
    : ""
  return {
    id: crypto.randomUUID(),
    path: file.path,
    lineLabel,
    language,
    snippet,
  }
}

/**
 * Build chat attachments from the selected range. A range can span from a
 * deletion to an addition (side !== endSide) when dragging across a replaced
 * block; slicing one file by start..end would paste the wrong lines, so each
 * side is collected separately.
 */
export function buildSelectionAttachments(
  file: ReviewDiffFile,
  range: SelectedLineRange
): Array<ChatAttachment> {
  const startSide = range.side ?? "additions"
  const endSide = range.endSide ?? startSide
  if (startSide === endSide) {
    return [makeSideAttachment(file, startSide, range.start, range.end)]
  }
  const deletionLine = startSide === "deletions" ? range.start : range.end
  const additionLine = startSide === "additions" ? range.start : range.end
  return [
    makeSideAttachment(file, "deletions", deletionLine, deletionLine),
    makeSideAttachment(file, "additions", additionLine, additionLine),
  ]
}

interface ShadowRootWithSelection {
  getSelection?: () => Selection | null
}

/**
 * Read the active selection inside a <diffs-container>'s open shadow root.
 * Chromium exposes ShadowRoot.getSelection(); elsewhere fall back to the
 * document selection (events from open shadow DOM are composed/retargeted).
 */
export function readDiffSelection(
  container: Element | null | undefined
): Selection | null {
  const root = container?.shadowRoot
  if (root) {
    const scoped = (root as ShadowRoot & ShadowRootWithSelection).getSelection
    if (typeof scoped === "function") return scoped.call(root)
  }
  return typeof document !== "undefined" ? document.getSelection() : null
}

/**
 * Map a selection boundary node to its file line number + side via the
 * data-line / data-line-type attributes Pierre stamps on every line div.
 */
export function lineMetaFromNode(
  node: Node | null
): { line: number; side: SelectionSide } | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null)
  const lineEl = el?.closest("[data-line]")
  if (!lineEl) return null
  const line = Number(lineEl.getAttribute("data-line"))
  if (!Number.isInteger(line)) return null
  const type = lineEl.getAttribute("data-line-type") ?? ""
  return { line, side: type.includes("deletion") ? "deletions" : "additions" }
}

/**
 * Resolve the current native text selection inside a diff to a line range, so a
 * plain text highlight can drive "Add to Chat" (Devin-style) instead of a
 * gutter drag.
 */
export function selectedRangeFromDiff(
  container: Element | null | undefined
): SelectedLineRange | null {
  const selection = readDiffSelection(container)
  if (!selection || selection.isCollapsed || selection.rangeCount === 0)
    return null
  const range = selection.getRangeAt(0)
  const start = lineMetaFromNode(range.startContainer)
  const end = lineMetaFromNode(range.endContainer)
  if (!start || !end) return null
  return {
    start: start.line,
    side: start.side,
    end: end.line,
    endSide: end.side,
  }
}

function selectionSideToGithub(
  side: SelectionSide | undefined
): "LEFT" | "RIGHT" {
  return side === "deletions" ? "LEFT" : "RIGHT"
}

/**
 * Map a Pierre selection range to a GitHub inline-comment payload. GitHub
 * forbids multi-line ranges that span sides, so a cross-side selection collapses
 * to a single line on the end side; same-side ranges keep their start_line.
 */
export function buildCommentPayload(
  path: string,
  range: SelectedLineRange,
  body: string
): ReviewCommentCreate {
  const startSide = range.side ?? "additions"
  const endSide = range.endSide ?? startSide
  if (startSide !== endSide) {
    return {
      path,
      line: range.end,
      side: selectionSideToGithub(endSide),
      body,
      start_line: null,
      start_side: null,
    }
  }
  const side = selectionSideToGithub(endSide)
  const lo = Math.min(range.start, range.end)
  const hi = Math.max(range.start, range.end)
  return {
    path,
    line: hi,
    side,
    body,
    start_line: lo < hi ? lo : null,
    start_side: lo < hi ? side : null,
  }
}

export function commentRangeLabel(range: SelectedLineRange): string {
  const side = (range.endSide ?? range.side) === "deletions" ? "L" : "R"
  const lo = Math.min(range.start, range.end)
  const hi = Math.max(range.start, range.end)
  return lo === hi ? `${side}${hi}` : `${side}${lo}-${hi}`
}
