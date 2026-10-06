import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"

import type { ReviewCommentCreate } from "@/lib/api"

function selectionSideToGithub(
  side: SelectionSide | undefined
): "LEFT" | "RIGHT" {
  return side === "deletions" ? "LEFT" : "RIGHT"
}

// Map a Pierre selection range to a GitHub inline-comment payload. GitHub
// forbids multi-line ranges that span sides, so a cross-side selection collapses
// to a single line on the end side; same-side ranges keep their start_line.
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
