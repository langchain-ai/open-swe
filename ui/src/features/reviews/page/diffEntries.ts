import { getSingularPatch } from "@pierre/diffs"
import type { DiffLineAnnotation, FileDiffMetadata } from "@pierre/diffs"

import type {
  PendingReviewComment,
  ReviewDiffFile,
  ReviewFinding,
  ReviewWalkthrough,
} from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import type { ProposedComment } from "@/features/reviews/lib/chatDiffActions"
import {
  rangeLineCount,
  walkthroughFileDiff,
} from "@/features/reviews/lib/walkthroughDiff"
import { isAnchored } from "./findings"
import type { CommentDraftTarget, DiffOrder } from "./store"

export interface EntryStep {
  index: number
  count: number
  title: string
  summary: string
  first: boolean
}

/** One rendered slice of the diff: a whole file, or a guide step's part of one. */
export interface DiffEntry {
  id: string
  file: ReviewDiffFile
  fileDiff: FileDiffMetadata
  additions: number
  deletions: number
  step: EntryStep | null
}

export type Note =
  | { kind: "step" }
  | { kind: "finding"; id: string }
  | { kind: "thread"; id: number }
  | { kind: "pending"; id: number }
  | { kind: "draft"; id: string }
  | { kind: "composer" }

export type NoteAnnotation = DiffLineAnnotation<Note>

/** Tree order, folders before files at each level, so the diff reads like the navigator. */
export function compareTreePaths(a: string, b: string): number {
  const left = a.split("/")
  const right = b.split("/")
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const leftIsDir = i < left.length - 1
    const rightIsDir = i < right.length - 1
    if (leftIsDir !== rightIsDir) return leftIsDir ? -1 : 1
    const order = left[i]!.localeCompare(right[i]!)
    if (order !== 0) return order
  }
  return left.length - right.length
}

const parsed = new WeakMap<ReviewDiffFile, FileDiffMetadata>()

function emptyDiff(file: ReviewDiffFile): FileDiffMetadata {
  const header = `diff --git a/${file.previousPath ?? file.path} b/${file.path}\n--- a/${file.previousPath ?? file.path}\n+++ b/${file.path}\n`
  return getSingularPatch(header)
}

export function wholeFileDiff(file: ReviewDiffFile): FileDiffMetadata {
  const cached = parsed.get(file)
  if (cached) return cached
  let diff: FileDiffMetadata
  try {
    diff = file.patch ? getSingularPatch(file.patch) : emptyDiff(file)
    // An added or deleted file's patch is the whole file, so there is no hidden context to offer.
    if (file.patch && (file.status === "added" || file.status === "removed"))
      diff.isPartial = false
  } catch (error) {
    console.warn("Could not parse a file's patch", { path: file.path, error })
    diff = emptyDiff(file)
  }
  parsed.set(file, diff)
  return diff
}

export function isRenderable(file: ReviewDiffFile): boolean {
  return !file.unrenderable && file.patch !== null
}

export function buildEntries(
  files: ReadonlyArray<ReviewDiffFile>,
  walkthrough: ReviewWalkthrough | null,
  order: DiffOrder
): Array<DiffEntry> {
  const sorted = [...files].sort((a, b) => compareTreePaths(a.path, b.path))
  const whole = (
    file: ReviewDiffFile,
    step: EntryStep | null,
    idPrefix = ""
  ): DiffEntry => ({
    id: `${idPrefix}${file.path}`,
    file,
    fileDiff: wholeFileDiff(file),
    additions: file.additions,
    deletions: file.deletions,
    step,
  })
  if (order === "files" || !walkthrough || walkthrough.steps.length === 0)
    return sorted.map((file) => whole(file, null))

  const byPath = new Map(sorted.map((file) => [file.path, file]))
  const seen = new Set<string>()
  const shownWhole = new Set<string>()
  const groups: Array<{
    title: string
    summary: string
    entries: Array<Omit<DiffEntry, "step" | "id">>
  }> = []
  let other: (typeof groups)[number] | null = null
  for (const step of walkthrough.steps) {
    const entries: Array<Omit<DiffEntry, "step" | "id">> = []
    for (const lines of step.files) {
      const file = byPath.get(lines.path)
      if (!file) continue
      // "Other changes" is for what the steps left out, not a second copy.
      if (step.other && shownWhole.has(file.path)) continue
      seen.add(file.path)
      const hasLines = lines.added.length > 0 || lines.deleted.length > 0
      const sliced =
        hasLines && isRenderable(file) ? walkthroughFileDiff(file, lines) : null
      if (!sliced) shownWhole.add(file.path)
      entries.push({
        file,
        fileDiff: sliced ?? wholeFileDiff(file),
        additions: sliced ? rangeLineCount(lines.added) : file.additions,
        deletions: sliced ? rangeLineCount(lines.deleted) : file.deletions,
      })
    }
    if (entries.length === 0) continue
    const group = {
      title: step.title,
      summary: step.other ? "" : step.summary,
      entries,
    }
    if (step.other) other = group
    groups.push(group)
  }
  const leftover = sorted
    .filter((file) => !seen.has(file.path))
    .map((file) => ({
      file,
      fileDiff: wholeFileDiff(file),
      additions: file.additions,
      deletions: file.deletions,
    }))
  if (leftover.length > 0) {
    if (other) other.entries.push(...leftover)
    else
      groups.push({ title: "Everything else", summary: "", entries: leftover })
  }
  return groups.flatMap((group, groupIndex) =>
    group.entries.map((entry, i) => ({
      ...entry,
      id: `${groupIndex + 1}:${entry.file.path}`,
      step: {
        index: groupIndex + 1,
        count: groups.length,
        title: group.title,
        summary: group.summary,
        first: i === 0,
      },
    }))
  )
}

export function containsLine(
  diff: FileDiffMetadata,
  line: number,
  side: "LEFT" | "RIGHT"
): boolean {
  return diff.hunks.some((hunk) =>
    side === "LEFT"
      ? line >= hunk.deletionStart &&
        line < hunk.deletionStart + hunk.deletionCount
      : line >= hunk.additionStart &&
        line < hunk.additionStart + hunk.additionCount
  )
}

const toSide = (side: "LEFT" | "RIGHT") =>
  side === "LEFT" ? "deletions" : "additions"

export interface NoteSources {
  findings: ReadonlyArray<ReviewFinding>
  threads: ReadonlyArray<ReviewThread>
  pending: ReadonlyArray<PendingReviewComment>
  drafts: ReadonlyArray<ProposedComment>
  composer: CommentDraftTarget | null
}

/** Every note on one entry's lines. A line in two guide slices gets its note in the slice that shows it. */
export function entryNotes(
  entry: DiffEntry,
  sources: NoteSources
): Array<NoteAnnotation> {
  const path = entry.file.path
  const notes: Array<NoteAnnotation> = []
  const shows = (line: number, side: "LEFT" | "RIGHT") =>
    entry.step === null || containsLine(entry.fileDiff, line, side)
  if (entry.step?.first)
    notes.push({ side: "additions", lineNumber: 0, metadata: { kind: "step" } })
  const findingThreads = new Set<number>()
  for (const finding of sources.findings) {
    if (finding.file !== path || !isAnchored(finding)) continue
    if (finding.github_review_comment_id !== null)
      findingThreads.add(finding.github_review_comment_id)
    const line = finding.end_line as number
    if (!shows(line, finding.side)) continue
    notes.push({
      side: toSide(finding.side),
      lineNumber: line,
      metadata: { kind: "finding", id: finding.id },
    })
  }
  for (const thread of sources.threads) {
    if (thread.path !== path || thread.outdated || thread.line === null)
      continue
    if (findingThreads.has(thread.id) || !shows(thread.line, thread.side))
      continue
    notes.push({
      side: toSide(thread.side),
      lineNumber: thread.line,
      metadata: { kind: "thread", id: thread.id },
    })
  }
  for (const comment of sources.pending) {
    if (comment.path !== path || comment.line === null) continue
    const side = comment.side ?? "RIGHT"
    if (!shows(comment.line, side)) continue
    notes.push({
      side: toSide(side),
      lineNumber: comment.line,
      metadata: { kind: "pending", id: comment.id },
    })
  }
  for (const draft of sources.drafts) {
    if (
      draft.range.file !== path ||
      !shows(draft.range.endLine, draft.range.side)
    )
      continue
    notes.push({
      side: toSide(draft.range.side),
      lineNumber: draft.range.endLine,
      metadata: { kind: "draft", id: draft.id },
    })
  }
  if (sources.composer?.path === path) {
    const { range } = sources.composer
    const side = range.endSide ?? range.side ?? "additions"
    const line = Math.max(range.start, range.end)
    if (shows(line, side === "deletions" ? "LEFT" : "RIGHT"))
      notes.push({ side, lineNumber: line, metadata: { kind: "composer" } })
  }
  return notes
}

export function notesSignature(notes: ReadonlyArray<NoteAnnotation>): string {
  return notes
    .map((note) => {
      const meta = note.metadata
      const id = "id" in meta ? meta.id : ""
      return `${meta.kind}:${id}:${note.side}:${note.lineNumber}`
    })
    .join("|")
}

export function matchesFileFilter(path: string, filter: string): boolean {
  const query = filter.trim().toLowerCase()
  return !query || path.toLowerCase().includes(query)
}

/** Only entries whose file matches; each step keeps its intro on its first remaining entry. */
export function filterEntries(
  entries: ReadonlyArray<DiffEntry>,
  filter: string
): Array<DiffEntry> {
  if (!filter.trim()) return [...entries]
  const introduced = new Set<number>()
  return entries
    .filter((entry) => matchesFileFilter(entry.file.path, filter))
    .map((entry) => {
      if (!entry.step) return entry
      const first = !introduced.has(entry.step.index)
      introduced.add(entry.step.index)
      return first === entry.step.first
        ? entry
        : { ...entry, step: { ...entry.step, first } }
    })
}
