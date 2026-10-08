import type { FindingGroup, ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"

export const findingGroupLabel: Record<FindingGroup, string> = {
  bug: "Bug",
  investigate: "Look into",
  informational: "FYI",
}

/** The severity rule colour, as a CSS colour so margins and dots share it. */
export const findingGroupColor: Record<FindingGroup, string> = {
  bug: "var(--destructive)",
  investigate: "var(--warning)",
  informational: "var(--muted-foreground)",
}

/** The same severities, at text contrast. */
export const findingGroupTextColor: Record<FindingGroup, string> = {
  bug: "var(--destructive-foreground)",
  investigate: "var(--warning-foreground)",
  informational: "var(--muted-foreground)",
}

const groupRank: Record<FindingGroup, number> = {
  bug: 0,
  investigate: 1,
  informational: 2,
}

export function isAnchored(finding: ReviewFinding): boolean {
  return finding.in_diff && finding.end_line !== null && !finding.outdated
}

export function findingLocation(finding: ReviewFinding): string {
  const name = finding.file.split("/").pop() ?? finding.file
  if (finding.end_line === null) return name
  return finding.start_line !== null && finding.start_line !== finding.end_line
    ? `${name}:${finding.start_line}-${finding.end_line}`
    : `${name}:${finding.end_line}`
}

/** Open before settled, then bugs before flags; settled findings sink, as on Devin. */
export function rankFindings(
  findings: ReadonlyArray<ReviewFinding>
): Array<ReviewFinding> {
  return [...findings].sort(
    (a, b) =>
      Number(a.status !== "open") - Number(b.status !== "open") ||
      groupRank[a.group] - groupRank[b.group] ||
      a.file.localeCompare(b.file) ||
      (a.end_line ?? 0) - (b.end_line ?? 0)
  )
}

export function askAboutFinding(finding: ReviewFinding): string {
  return `About Open SWE's finding "${finding.title}" at \`${finding.file}${finding.end_line !== null ? `:${finding.end_line}` : ""}\`: `
}

export function fixFinding(finding: ReviewFinding): string {
  return `Fix Open SWE's finding "${finding.title}" at \`${finding.file}${finding.end_line !== null ? `:${finding.end_line}` : ""}\` on this branch.`
}

/** Open, current review threads, minus the GitHub copies of Open SWE's own findings. */
export function threadsNeedingAttention(
  threads: ReadonlyArray<ReviewThread>,
  findings: ReadonlyArray<ReviewFinding>
): Array<ReviewThread> {
  const mirrored = new Set(
    findings.flatMap((finding) => finding.github_review_comment_id ?? [])
  )
  return threads.filter(
    (thread) =>
      !thread.resolved &&
      !thread.outdated &&
      thread.line !== null &&
      !mirrored.has(thread.id)
  )
}

/** Unresolved conversations people still need to look at, split from the outdated ones. */
export function openConversationCounts(
  threads: ReadonlyArray<ReviewThread>,
  findings: ReadonlyArray<ReviewFinding>
): { current: number; outdated: number } {
  const mirrored = new Set(
    findings.flatMap((finding) => finding.github_review_comment_id ?? [])
  )
  let current = 0
  let outdated = 0
  for (const thread of threads) {
    if (thread.resolved || mirrored.has(thread.id)) continue
    if (thread.outdated) outdated += 1
    else current += 1
  }
  return { current, outdated }
}
