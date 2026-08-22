/**
 * Reconciling the AI-sorted diff groups with the diff actually on screen.
 *
 * Groups are generated for one head commit and stored; the diff can move under
 * them. Resolving drops what no longer exists and sweeps whatever the groups
 * missed into a trailing bucket, so every changed file appears exactly once.
 */

import type {
  ReviewDiffFile,
  ReviewDiffGroup,
} from "@/features/reviews/lib/api"

export interface ResolvedGroup {
  index: number
  title: string
  summary: string
  files: Array<ReviewDiffFile>
  additions: number
  deletions: number
}

/**
 * Null when the diff isn't loaded or the groups can't be used (stale, empty, or
 * nothing survived resolution) — the caller then falls back to the file tree.
 */
export function resolveDiffGroups(
  diffFiles: Array<ReviewDiffFile> | null,
  groups: Array<ReviewDiffGroup>,
  stale: boolean
): Array<ResolvedGroup> | null {
  if (!diffFiles || stale || groups.length === 0) return null
  const byPath = new Map(diffFiles.map((file) => [file.path, file]))
  const assigned = new Set<string>()
  const resolved: Array<Omit<ResolvedGroup, "index">> = []
  for (const group of groups) {
    const files: Array<ReviewDiffFile> = []
    for (const path of group.files) {
      const file = byPath.get(path)
      if (file && !assigned.has(path)) {
        assigned.add(path)
        files.push(file)
      }
    }
    if (files.length === 0) continue
    resolved.push({
      title: group.title,
      summary: group.summary,
      files,
      additions: files.reduce((acc, file) => acc + file.additions, 0),
      deletions: files.reduce((acc, file) => acc + file.deletions, 0),
    })
  }
  const leftover = diffFiles.filter((file) => !assigned.has(file.path))
  if (leftover.length > 0) {
    resolved.push({
      title: "Other changes",
      summary: "",
      files: leftover,
      additions: leftover.reduce((acc, file) => acc + file.additions, 0),
      deletions: leftover.reduce((acc, file) => acc + file.deletions, 0),
    })
  }
  if (resolved.length === 0) return null
  return resolved.map((group, i) => ({ ...group, index: i + 1 }))
}

/**
 * Older stored summaries embed `[label](#loc=path:line)` diff links; render the
 * label as inline code instead so no stale jump-links leak into the block body.
 */
export function stripLocationLinks(summary: string): string {
  return summary.replace(/\[([^\]]+)\]\(#loc=[^)]*\)/g, "`$1`")
}
