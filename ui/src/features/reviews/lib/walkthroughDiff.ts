import { getSingularPatch, parseDiffFromFile } from "@pierre/diffs"
import type { FileDiffMetadata, Hunk } from "@pierre/diffs"

import type {
  ReviewDiffFile,
  ReviewLineRange,
  ReviewWalkthroughFile,
} from "@/lib/api"

function overlaps(
  start: number,
  count: number,
  ranges: Array<ReviewLineRange>
): boolean {
  if (count === 0) return false
  const end = start + count - 1
  return ranges.some(([from, to]) => from <= end && to >= start)
}

export function rangeLineCount(ranges: Array<ReviewLineRange>): number {
  return ranges.reduce((total, [from, to]) => total + to - from + 1, 0)
}

function patchLine(prefix: string, line: string | undefined): string {
  const text = line ?? ""
  return text.endsWith("\n")
    ? `${prefix}${text}`
    : `${prefix}${text}\n\\ No newline at end of file\n`
}

function hunkPatch(meta: FileDiffMetadata, hunk: Hunk): string {
  const context = hunk.hunkContext ? ` ${hunk.hunkContext}` : ""
  let out = `@@ -${hunk.deletionStart},${hunk.deletionCount} +${hunk.additionStart},${hunk.additionCount} @@${context}\n`
  for (const content of hunk.hunkContent) {
    if (content.type === "context") {
      for (let i = 0; i < content.lines; i++)
        out += patchLine(" ", meta.additionLines[content.additionLineIndex + i])
      continue
    }
    for (let i = 0; i < content.deletions; i++)
      out += patchLine("-", meta.deletionLines[content.deletionLineIndex + i])
    for (let i = 0; i < content.additions; i++)
      out += patchLine("+", meta.additionLines[content.additionLineIndex + i])
  }
  return out
}

function patchHeader(file: ReviewDiffFile): string {
  const oldPath = file.previousPath ?? file.path
  const lines = [`diff --git a/${oldPath} b/${file.path}`]
  if (file.status === "added") lines.push("new file mode 100644")
  if (file.status === "removed") lines.push("deleted file mode 100644")
  lines.push(file.status === "added" ? "--- /dev/null" : `--- a/${oldPath}`)
  lines.push(file.status === "removed" ? "+++ /dev/null" : `+++ b/${file.path}`)
  return `${lines.join("\n")}\n`
}

/**
 * The PR's own diff of `file`, cut down to the hunks holding the step's lines.
 *
 * Built from the full-file diff so every line keeps its real PR number, which
 * is what findings, comments and selections anchor to. Returns `null` when the
 * whole file belongs to the step (or nothing matches), meaning: render it whole.
 */
export function walkthroughFileDiff(
  file: ReviewDiffFile,
  lines: ReviewWalkthroughFile
): FileDiffMetadata | null {
  if (lines.added.length === 0 && lines.deleted.length === 0) return null
  const full = parseDiffFromFile(
    { name: file.path, contents: file.originalContent },
    { name: file.path, contents: file.modifiedContent }
  )
  const kept = full.hunks.filter(
    (hunk) =>
      overlaps(hunk.additionStart, hunk.additionCount, lines.added) ||
      overlaps(hunk.deletionStart, hunk.deletionCount, lines.deleted)
  )
  if (kept.length === 0 || kept.length === full.hunks.length) return null
  return getSingularPatch(
    patchHeader(file) + kept.map((hunk) => hunkPatch(full, hunk)).join("")
  )
}
