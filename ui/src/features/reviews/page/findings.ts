import type { FindingGroup, ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import type { LineTarget } from "./store"
import { splitPath, withLine } from "./text"

export const findingGroupLabel: Record<FindingGroup, string> = {
  bug: "Bug",
  investigate: "Look into",
  informational: "FYI",
}

/** The severity rule colour, as a CSS colour so margins and dots share it. */
export const findingGroupColor: Record<FindingGroup, string> = {
  bug: "var(--icon-error)",
  investigate: "var(--icon-warning)",
  informational: "var(--icon-tertiary)",
}

/** The same severities, at text contrast. */
export const findingGroupTextColor: Record<FindingGroup, string> = {
  bug: "var(--text-error-secondary)",
  investigate: "var(--text-warning-secondary)",
  informational: "var(--text-secondary)",
}

const groupRank: Record<FindingGroup, number> = {
  bug: 0,
  investigate: 1,
  informational: 2,
}

export type AnchoredFinding = ReviewFinding & { end_line: number }

/** A finding on a line the diff still shows. */
export function isAnchored(finding: ReviewFinding): finding is AnchoredFinding {
  return finding.in_diff && finding.end_line !== null && !finding.outdated
}

export function isOpenAnchored(
  finding: ReviewFinding
): finding is AnchoredFinding {
  return finding.status === "open" && isAnchored(finding)
}

export function openFindingCounts(findings: ReadonlyArray<ReviewFinding>): {
  bugs: number
  flags: number
} {
  const open = findings.filter((finding) => finding.status === "open")
  const bugs = open.filter((finding) => finding.group === "bug").length
  return { bugs, flags: open.length - bugs }
}

export function findingLocation(finding: ReviewFinding): string {
  const { name } = splitPath(finding.file)
  return finding.start_line !== null &&
    finding.end_line !== null &&
    finding.start_line !== finding.end_line
    ? `${name}:${finding.start_line}-${finding.end_line}`
    : withLine(name, finding.end_line)
}

/** Open before settled, then bugs before flags; settled findings sink. */
export function rankFindings<T extends ReviewFinding>(
  findings: ReadonlyArray<T>
): Array<T> {
  return [...findings].sort(
    (a, b) =>
      Number(a.status !== "open") - Number(b.status !== "open") ||
      groupRank[a.group] - groupRank[b.group] ||
      a.file.localeCompare(b.file) ||
      (a.end_line ?? 0) - (b.end_line ?? 0)
  )
}

function findingRef(finding: ReviewFinding): string {
  return `\`${withLine(finding.file, finding.end_line)}\``
}

export function askAboutFinding(finding: ReviewFinding): string {
  return `About Open SWE's finding "${finding.title}" at ${findingRef(finding)}: `
}

export function fixFinding(finding: ReviewFinding): string {
  return `Fix Open SWE's finding "${finding.title}" at ${findingRef(finding)} on this branch.`
}

export function findingTarget(finding: AnchoredFinding): LineTarget {
  return {
    kind: "line",
    path: finding.file,
    line: finding.end_line,
    start: finding.start_line ?? undefined,
    side: finding.side,
  }
}

export type PlacedThread = ReviewThread & { line: number }

/** Whether the thread still sits on a line of the current diff. */
export function isPlaced(thread: ReviewThread): thread is PlacedThread {
  return !thread.outdated && thread.line !== null
}

export function threadTarget(thread: PlacedThread): LineTarget {
  return {
    kind: "line",
    path: thread.path,
    line: thread.line,
    start: thread.start_line ?? undefined,
    side: thread.side,
  }
}

/** The thread's line, or the one it was first left on once it's outdated. */
export function threadLine(thread: ReviewThread): number | null {
  return thread.line ?? thread.original_line
}

/** "routes.py:53": the file name and line, for where the full path won't fit. */
export function threadLocation(thread: ReviewThread): string {
  return withLine(splitPath(thread.path).name, threadLine(thread))
}

/** The review threads that are GitHub copies of these findings. */
export function mirroredThreadIds(
  findings: ReadonlyArray<ReviewFinding>
): Set<number> {
  return new Set(
    findings.flatMap((finding) => finding.github_review_comment_id ?? [])
  )
}

/** Unresolved threads, less the GitHub copies of Open SWE's findings, which count as findings. */
export function openConversations(
  threads: ReadonlyArray<ReviewThread>,
  findings: ReadonlyArray<ReviewFinding>
): Array<ReviewThread> {
  const mirrored = mirroredThreadIds(findings)
  return threads.filter(
    (thread) => !thread.resolved && !mirrored.has(thread.id)
  )
}

/** Open conversations still on a line of the current diff. */
export function threadsNeedingAttention(
  threads: ReadonlyArray<ReviewThread>,
  findings: ReadonlyArray<ReviewFinding>
): Array<PlacedThread> {
  return openConversations(threads, findings).filter(isPlaced)
}
