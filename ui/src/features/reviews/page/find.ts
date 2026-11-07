import { FuzzySearch } from "@/lib/fuzzySearch"
import type { ReviewDiffFile } from "@/lib/api"
import type { DiffSide } from "@/features/reviews/lib/chatDiffActions"
import { entryShows, type DiffEntry } from "./diffEntries"
import type { DiffTarget } from "./store"

/** One hit of the page's find: a file whose path matches, or a diff line whose text does. */
export type FindMatch =
  | { kind: "file"; path: string }
  | { kind: "line"; path: string; line: number; side: DiffSide }

interface PatchLine {
  line: number
  side: DiffSide
  text: string
}

const patchLines = new WeakMap<ReviewDiffFile, ReadonlyArray<PatchLine>>()

/** The lines a file's patch shows, numbered as the diff numbers them; context counts as the head's. */
function linesOf(file: ReviewDiffFile): ReadonlyArray<PatchLine> {
  const known = patchLines.get(file)
  if (known) return known
  const lines: Array<PatchLine> = []
  // Null until the first hunk, so a git header's ---/+++ lines aren't read as changes.
  let left: number | null = null
  let right = 0
  for (const raw of file.patch?.split("\n") ?? []) {
    const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw)
    if (header) {
      left = Number(header[1])
      right = Number(header[2])
    } else if (left === null) {
      continue
    } else if (raw.startsWith("+")) {
      lines.push({ line: right++, side: "RIGHT", text: raw.slice(1) })
    } else if (raw.startsWith("-")) {
      lines.push({ line: left++, side: "LEFT", text: raw.slice(1) })
    } else if (raw.startsWith(" ")) {
      lines.push({ line: right, side: "RIGHT", text: raw.slice(1) })
      left++
      right++
    }
  }
  patchLines.set(file, lines)
  return lines
}

/**
 * Every hit for the query in what the view shows, in its reading order: a
 * file's path first, then its lines. A guide slice claims only the lines it
 * shows, so a file split across steps is searched once, step by step.
 */
export function findMatches(
  entries: ReadonlyArray<DiffEntry>,
  query: string
): Array<FindMatch> {
  if (!query.trim()) return []
  const needle = query.toLowerCase()
  const search = new FuzzySearch(needle)
  const matches: Array<FindMatch> = []
  const exact: Array<FindMatch> = []
  const seenFiles = new Set<string>()
  const seenLines = new Set<string>()
  for (const entry of entries) {
    const { path } = entry.file
    if (!seenFiles.has(path)) {
      seenFiles.add(path)
      if (search.matches(path)) {
        const match: FindMatch = { kind: "file", path }
        matches.push(match)
        if (path.toLowerCase().includes(needle)) exact.push(match)
      }
    }
    for (const { line, side, text } of linesOf(entry.file)) {
      const key = `${path}:${side}:${line}`
      if (seenLines.has(key) || !entryShows(entry, path, line, side)) continue
      seenLines.add(key)
      if (search.ranges(text).length) {
        const match: FindMatch = { kind: "line", path, line, side }
        matches.push(match)
        if (text.toLowerCase().includes(needle)) exact.push(match)
      }
    }
  }
  return exact.length ? exact : matches
}

export function findTarget(
  match: FindMatch
): Exclude<DiffTarget, { kind: "top" | "entry" }> {
  return match.kind === "file"
    ? { kind: "file", path: match.path }
    : { kind: "line", path: match.path, line: match.line, side: match.side }
}
