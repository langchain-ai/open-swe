import { getSingularPatch } from "@pierre/diffs"
import type { FileDiffMetadata, Hunk } from "@pierre/diffs"

import type {
  ReviewDiffFile,
  ReviewLineRange,
  ReviewWalkthroughFile,
} from "@/lib/api"
import { hashFileContents } from "@/features/agents/utils/diffUtils"

function overlaps(
  start: number,
  count: number,
  ranges: Array<ReviewLineRange>
): boolean {
  if (count === 0) return false
  const end = start + count - 1
  return ranges.some(([from, to]) => from <= end && to >= start)
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

export function patchHeader(file: ReviewDiffFile): string {
  const oldPath = file.previousPath ?? file.path
  const lines = [`diff --git a/${oldPath} b/${file.path}`]
  if (file.status === "added") lines.push("new file mode 100644")
  if (file.status === "removed") lines.push("deleted file mode 100644")
  lines.push(file.status === "added" ? "--- /dev/null" : `--- a/${oldPath}`)
  lines.push(file.status === "removed" ? "+++ /dev/null" : `+++ b/${file.path}`)
  return `${lines.join("\n")}\n`
}

const parsed = new WeakMap<ReviewDiffFile, FileDiffMetadata>()

function fullDiff(file: ReviewDiffFile, patch: string): FileDiffMetadata {
  const cached = parsed.get(file)
  if (cached) return cached
  const diff = getSingularPatch(patch)
  parsed.set(file, diff)
  return diff
}

/**
 * Indexes of the PR's hunks of `file` that hold any of the step's lines.
 *
 * A step owns whole hunks, so these are what it shows. `null` when there is
 * nothing to match against (no lines, no patch, or no hunk holds them),
 * meaning: render the file whole.
 */
export function walkthroughHunks(
  file: ReviewDiffFile,
  lines: ReviewWalkthroughFile
): Array<number> | null {
  if (lines.added.length === 0 && lines.deleted.length === 0) return null
  if (!file.patch) return null
  const held = fullDiff(file, file.patch)
    .hunks.map((hunk, index) =>
      overlaps(hunk.additionStart, hunk.additionCount, lines.added) ||
      overlaps(hunk.deletionStart, hunk.deletionCount, lines.deleted)
        ? index
        : -1
    )
    .filter((index) => index >= 0)
  return held.length > 0 ? held : null
}

const slices = new WeakMap<
  ReviewDiffFile,
  Map<string, FileDiffMetadata | null>
>()

/**
 * The PR's own diff of `file`, cut down to the hunks at `indexes`.
 *
 * Built from the full-file diff so every line keeps its real PR number, which
 * is what findings, comments and selections anchor to. Returns `null` when
 * that is every hunk, meaning: render it whole.
 */
export function walkthroughFileDiff(
  file: ReviewDiffFile,
  indexes: ReadonlyArray<number>
): FileDiffMetadata | null {
  if (!file.patch) return null
  // Reusing the object keeps its hydrated contents across re-renders and order switches.
  const key = indexes.join(",")
  const cache = slices.get(file) ?? new Map<string, FileDiffMetadata | null>()
  slices.set(file, cache)
  if (!cache.has(key)) cache.set(key, sliceFileDiff(file, file.patch, indexes))
  return cache.get(key) ?? null
}

function sliceFileDiff(
  file: ReviewDiffFile,
  patch: string,
  indexes: ReadonlyArray<number>
): FileDiffMetadata | null {
  const full = fullDiff(file, patch)
  const kept = full.hunks.filter((_, index) => indexes.includes(index))
  if (kept.length === 0 || kept.length === full.hunks.length) return null
  const slice =
    patchHeader(file) + kept.map((hunk) => hunkPatch(full, hunk)).join("")
  const sliced = getSingularPatch(slice)
  // Hydration keys its highlight cache on this; without it a slice reuses the whole file's rows.
  sliced.cacheKey = `${file.path}:slice:${hashFileContents(slice)}`
  return sliced
}

/** Changed lines in `diff`, without its context. */
export function changeCounts(diff: FileDiffMetadata): {
  additions: number
  deletions: number
} {
  let additions = 0
  let deletions = 0
  for (const hunk of diff.hunks)
    for (const content of hunk.hunkContent)
      if (content.type !== "context") {
        additions += content.additions
        deletions += content.deletions
      }
  return { additions, deletions }
}
