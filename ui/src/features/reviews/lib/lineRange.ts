import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"

import type { ReviewCommentCreate } from "@/lib/api"
import type { DiffSide } from "@/features/reviews/lib/chatDiffActions"

export function toGithubSide(side: SelectionSide | undefined): DiffSide {
  return side === "deletions" ? "LEFT" : "RIGHT"
}

export function toPierreSide(side: DiffSide): SelectionSide {
  return side === "LEFT" ? "deletions" : "additions"
}

/** A selection's lowest and highest line, on the side its end sits on. */
export function rangeBounds(range: SelectedLineRange): {
  lo: number
  hi: number
  side: DiffSide
} {
  return {
    lo: Math.min(range.start, range.end),
    hi: Math.max(range.start, range.end),
    side: toGithubSide(range.endSide ?? range.side),
  }
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
      side: toGithubSide(endSide),
      body,
      start_line: null,
      start_side: null,
    }
  }
  const { lo, hi, side } = rangeBounds(range)
  return {
    path,
    line: hi,
    side,
    body,
    start_line: lo < hi ? lo : null,
    start_side: lo < hi ? side : null,
  }
}

/** "line 8", "lines 20–24", with "old" for the deleted side. */
export function readableRangeLabel(range: SelectedLineRange): string {
  const { lo, hi, side } = rangeBounds(range)
  const old = side === "LEFT" ? "old " : ""
  return lo === hi ? `${old}line ${hi}` : `${old}lines ${lo}–${hi}`
}

export function commentRangeLabel(range: SelectedLineRange): string {
  const { lo, hi, side } = rangeBounds(range)
  const letter = side === "LEFT" ? "L" : "R"
  return lo === hi ? `${letter}${hi}` : `${letter}${lo}-${hi}`
}
