import { getSingularPatch } from "@pierre/diffs"
import type { DiffLineAnnotation, FileDiffMetadata } from "@pierre/diffs"

import type {
  PendingReviewComment,
  ReviewDiffFile,
  ReviewFinding,
  ReviewWalkthrough,
} from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import type {
  DiffSide,
  ProposedComment,
} from "@/features/reviews/lib/chatDiffActions"
import { rangeBounds, toPierreSide } from "@/features/reviews/lib/lineRange"
import {
  patchHeader,
  rangeLineCount,
  walkthroughFileDiff,
} from "@/features/reviews/lib/walkthroughDiff"
import { isAnchored, mirroredThreadIds, type AnchoredFinding } from "./findings"
import type { CommentDraftTarget, DiffOrder, DiffTarget } from "./store"

interface EntryStep {
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

type NoteAnnotation = DiffLineAnnotation<Note>

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

function wholeFileDiff(file: ReviewDiffFile): FileDiffMetadata {
  const cached = parsed.get(file)
  if (cached) return cached
  let diff: FileDiffMetadata
  try {
    diff = getSingularPatch(file.patch ?? patchHeader(file))
    // An added or deleted file's patch is the whole file, so there is no hidden context to offer.
    if (file.patch && (file.status === "added" || file.status === "removed"))
      diff.isPartial = false
  } catch (error) {
    console.warn("Could not parse a file's patch", { path: file.path, error })
    diff = getSingularPatch(patchHeader(file))
  }
  parsed.set(file, diff)
  return diff
}

export function isRenderable(file: ReviewDiffFile): boolean {
  return !file.unrenderable && file.patch !== null
}

type EntryBody = Omit<DiffEntry, "step" | "id">

function whole(file: ReviewDiffFile): EntryBody {
  return {
    file,
    fileDiff: wholeFileDiff(file),
    additions: file.additions,
    deletions: file.deletions,
  }
}

export function buildEntries(
  files: ReadonlyArray<ReviewDiffFile>,
  walkthrough: ReviewWalkthrough | null,
  order: DiffOrder
): Array<DiffEntry> {
  const sorted = [...files].sort((a, b) => compareTreePaths(a.path, b.path))
  if (order === "files" || !walkthrough || walkthrough.steps.length === 0)
    return sorted.map((file) => ({ ...whole(file), id: file.path, step: null }))

  const byPath = new Map(sorted.map((file) => [file.path, file]))
  const seen = new Set<string>()
  const shownWhole = new Set<string>()
  const groups: Array<{
    title: string
    summary: string
    entries: EntryBody[]
  }> = []
  let other: (typeof groups)[number] | null = null
  for (const step of walkthrough.steps) {
    const entries: Array<EntryBody> = []
    for (const lines of step.files) {
      const file = byPath.get(lines.path)
      if (!file) continue
      // "Other changes" is for what the steps left out, not a second copy.
      if (step.other && shownWhole.has(file.path)) continue
      seen.add(file.path)
      const sliced = file.unrenderable ? null : walkthroughFileDiff(file, lines)
      if (!sliced) {
        shownWhole.add(file.path)
        entries.push(whole(file))
        continue
      }
      entries.push({
        file,
        fileDiff: sliced,
        additions: rangeLineCount(lines.added),
        deletions: rangeLineCount(lines.deleted),
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
  const leftover = sorted.filter((file) => !seen.has(file.path)).map(whole)
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

function containsLine(
  diff: FileDiffMetadata,
  line: number,
  side: DiffSide
): boolean {
  return diff.hunks.some((hunk) =>
    side === "LEFT"
      ? line >= hunk.deletionStart &&
        line < hunk.deletionStart + hunk.deletionCount
      : line >= hunk.additionStart &&
        line < hunk.additionStart + hunk.additionCount
  )
}

/** Whether this entry shows the line: a whole file always does, a guide slice only in its hunks. */
export function entryShows(
  entry: DiffEntry,
  path: string,
  line: number,
  side: DiffSide
): boolean {
  return (
    entry.file.path === path &&
    (entry.step === null || containsLine(entry.fileDiff, line, side))
  )
}

/** The entry a jump lands on: the one showing the line, else the file's first. */
export function findEntry(
  entries: ReadonlyArray<DiffEntry>,
  target: Exclude<DiffTarget, { kind: "top" }>
): DiffEntry | undefined {
  if (target.kind === "entry")
    return entries.find((entry) => entry.id === target.id)
  const ofFile = (entry: DiffEntry) => entry.file.path === target.path
  if (target.kind === "file") return entries.find(ofFile)
  return (
    entries.find((entry) =>
      entryShows(entry, target.path, target.line, target.side)
    ) ?? entries.find(ofFile)
  )
}

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
  // Where a note goes on this entry, or null when the line isn't in it.
  const at = (side: DiffSide, line: number) =>
    entryShows(entry, path, line, side)
      ? { side: toPierreSide(side), lineNumber: line }
      : null
  if (entry.step?.first)
    notes.push({ side: "additions", lineNumber: 0, metadata: { kind: "step" } })
  const anchored = sources.findings.filter(
    (finding): finding is AnchoredFinding =>
      finding.file === path && isAnchored(finding)
  )
  for (const finding of anchored) {
    const spot = at(finding.side, finding.end_line)
    if (spot)
      notes.push({ ...spot, metadata: { kind: "finding", id: finding.id } })
  }
  // A thread that's a finding's GitHub copy shows as that finding.
  const mirrored = mirroredThreadIds(anchored)
  for (const thread of sources.threads) {
    if (thread.path !== path || thread.outdated || thread.line === null)
      continue
    const spot = mirrored.has(thread.id) ? null : at(thread.side, thread.line)
    if (spot)
      notes.push({ ...spot, metadata: { kind: "thread", id: thread.id } })
  }
  for (const comment of sources.pending) {
    if (comment.path !== path || comment.line === null) continue
    const spot = at(comment.side ?? "RIGHT", comment.line)
    if (spot)
      notes.push({ ...spot, metadata: { kind: "pending", id: comment.id } })
  }
  for (const draft of sources.drafts) {
    const spot =
      draft.range.file === path
        ? at(draft.range.side, draft.range.endLine)
        : null
    if (spot) notes.push({ ...spot, metadata: { kind: "draft", id: draft.id } })
  }
  if (sources.composer?.path === path) {
    const { hi, side } = rangeBounds(sources.composer.range)
    const spot = at(side, hi)
    if (spot) notes.push({ ...spot, metadata: { kind: "composer" } })
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
): ReadonlyArray<DiffEntry> {
  if (!filter.trim()) return entries
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
