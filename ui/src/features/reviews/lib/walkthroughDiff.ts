import { getSingularPatch } from "@pierre/diffs"
import type { FileDiffMetadata, Hunk } from "@pierre/diffs"

import type {
  ReviewDiffFile,
  ReviewLineRange,
  ReviewWalkthroughFile,
} from "@/lib/api"
import { hashFileContents } from "@/features/agents/utils/diffUtils"

// Unchanged lines shown around a step's lines, as in a hunk.
const CONTEXT_LINES = 3

export function rangeLineCount(ranges: Array<ReviewLineRange>): number {
  return ranges.reduce((total, [from, to]) => total + to - from + 1, 0)
}

function holds(ranges: Array<ReviewLineRange>, line: number): boolean {
  return ranges.some(([from, to]) => from <= line && line <= to)
}

function patchLine(prefix: string, line: string | undefined): string {
  const text = line ?? ""
  return text.endsWith("\n")
    ? `${prefix}${text}`
    : `${prefix}${text}\n\\ No newline at end of file\n`
}

/** One line of a hunk, with where it sits on each side: its own number, or the next line's. */
interface Row {
  sign: " " | "-" | "+"
  text: string | undefined
  old: number
  new: number
}

function hunkRows(meta: FileDiffMetadata, hunk: Hunk): Array<Row> {
  const rows: Array<Row> = []
  // An empty side's start is the line before it.
  let old = hunk.deletionStart + (hunk.deletionCount === 0 ? 1 : 0)
  let added = hunk.additionStart + (hunk.additionCount === 0 ? 1 : 0)
  for (const content of hunk.hunkContent) {
    if (content.type === "context") {
      for (let i = 0; i < content.lines; i++) {
        const text = meta.additionLines[content.additionLineIndex + i]
        rows.push({ sign: " ", text, old: old++, new: added++ })
      }
      continue
    }
    for (let i = 0; i < content.deletions; i++) {
      const text = meta.deletionLines[content.deletionLineIndex + i]
      rows.push({ sign: "-", text, old: old++, new: added })
    }
    for (let i = 0; i < content.additions; i++) {
      const text = meta.additionLines[content.additionLineIndex + i]
      rows.push({ sign: "+", text, old, new: added++ })
    }
  }
  return rows
}

/**
 * The runs of `rows` that hold the step's lines, each with a little context.
 *
 * A run never crosses a changed line the step does not hold, so a hunk the
 * plan split between steps shows each step only its own part.
 */
function stepRuns(
  rows: Array<Row>,
  mine: (row: Row) => boolean
): Array<Array<Row>> {
  const runs: Array<Array<Row>> = []
  let from = 0
  for (let i = 0; i <= rows.length; i++) {
    const row = rows[i]
    if (row && (row.sign === " " || mine(row))) continue
    const span = rows.slice(from, i)
    const first = span.findIndex((r) => r.sign !== " ")
    if (first >= 0) {
      const last =
        span.length - 1 - [...span].reverse().findIndex((r) => r.sign !== " ")
      runs.push(
        span.slice(Math.max(0, first - CONTEXT_LINES), last + 1 + CONTEXT_LINES)
      )
    }
    from = i + 1
  }
  return runs
}

function runPatch(run: Array<Row>, hunkContext: string | undefined): string {
  const olds = run.filter((row) => row.sign !== "+")
  const news = run.filter((row) => row.sign !== "-")
  const oldStart = olds[0]?.old ?? run[0]!.old - 1
  const newStart = news[0]?.new ?? run[0]!.new - 1
  const context = hunkContext ? ` ${hunkContext}` : ""
  let out = `@@ -${oldStart},${olds.length} +${newStart},${news.length} @@${context}\n`
  for (const row of run) out += patchLine(row.sign, row.text)
  return out
}

const slices = new WeakMap<
  ReviewDiffFile,
  Map<string, FileDiffMetadata | null>
>()

export function patchHeader(file: ReviewDiffFile): string {
  const oldPath = file.previousPath ?? file.path
  const lines = [`diff --git a/${oldPath} b/${file.path}`]
  if (file.status === "added") lines.push("new file mode 100644")
  if (file.status === "removed") lines.push("deleted file mode 100644")
  lines.push(file.status === "added" ? "--- /dev/null" : `--- a/${oldPath}`)
  lines.push(file.status === "removed" ? "+++ /dev/null" : `+++ b/${file.path}`)
  return `${lines.join("\n")}\n`
}

/**
 * The PR's own diff of `file`, cut down to exactly the step's lines and a
 * little context around them.
 *
 * Built from the full-file diff so every line keeps its real PR number, which
 * is what findings, comments and selections anchor to. Returns `null` when the
 * step holds every changed line of the file (or none of them), meaning: render
 * it whole.
 */
export function walkthroughFileDiff(
  file: ReviewDiffFile,
  lines: ReviewWalkthroughFile
): FileDiffMetadata | null {
  if (lines.added.length === 0 && lines.deleted.length === 0) return null
  if (!file.patch) return null
  // Reusing the object keeps its hydrated contents across re-renders and order switches.
  const key = JSON.stringify([lines.added, lines.deleted])
  const cache = slices.get(file) ?? new Map<string, FileDiffMetadata | null>()
  slices.set(file, cache)
  if (!cache.has(key)) cache.set(key, sliceFileDiff(file, file.patch, lines))
  return cache.get(key) ?? null
}

function sliceFileDiff(
  file: ReviewDiffFile,
  patch: string,
  lines: ReviewWalkthroughFile
): FileDiffMetadata | null {
  const full = getSingularPatch(patch)
  const mine = (row: Row) =>
    row.sign === "+"
      ? holds(lines.added, row.new)
      : row.sign === "-" && holds(lines.deleted, row.old)
  const hunks = full.hunks.map((hunk) => ({ hunk, rows: hunkRows(full, hunk) }))
  const changed = hunks.flatMap(({ rows }) =>
    rows.filter((r) => r.sign !== " ")
  )
  const held = changed.filter(mine).length
  if (held === 0 || held === changed.length) return null
  const slice =
    patchHeader(file) +
    hunks
      .flatMap(({ hunk, rows }) =>
        stepRuns(rows, mine).map((run) => runPatch(run, hunk.hunkContext))
      )
      .join("")
  const sliced = getSingularPatch(slice)
  // Hydration keys its highlight cache on this; without it a slice reuses the whole file's rows.
  sliced.cacheKey = `${file.path}:slice:${hashFileContents(slice)}`
  return sliced
}
