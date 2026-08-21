/**
 * Everything about a review finding that more than one part of the review page
 * needs: how each group is labelled, where a finding is anchored in the diff,
 * and the shared expand/collapse state.
 */

import { createContext, useContext } from "react"
import { BugBeetleIcon, FlagIcon, InfoIcon } from "@phosphor-icons/react"

import type { Icon } from "@phosphor-icons/react"
import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"
import type { FindingGroup, ReviewFinding } from "@/features/reviews/lib/api"

export interface GroupStyle {
  label: string
  className: string
  Icon: Icon
}

export const GROUP_STYLES: Record<FindingGroup, GroupStyle> = {
  bug: { label: "Bug", className: "text-destructive", Icon: BugBeetleIcon },
  investigate: {
    label: "Investigate",
    className: "text-amber-500",
    Icon: FlagIcon,
  },
  informational: {
    label: "Informational",
    className: "text-muted-foreground",
    Icon: InfoIcon,
  },
}

export function findingSide(finding: ReviewFinding): SelectionSide {
  return finding.side === "LEFT" ? "deletions" : "additions"
}

/** Whether the finding can be rendered inline at a line of the current diff. */
export function isAnchored(finding: ReviewFinding): boolean {
  return Boolean(finding.file) && finding.in_diff && finding.end_line !== null
}

export function findingAnchorLabel(finding: ReviewFinding): string {
  if (finding.start_line === null || finding.end_line === null)
    return finding.file
  if (finding.start_line === finding.end_line)
    return `${finding.file}:${finding.end_line}`
  return `${finding.file}:${finding.start_line}-${finding.end_line}`
}

export function findingSelectedRange(
  finding: ReviewFinding
): SelectedLineRange | null {
  if (finding.end_line === null) return null
  const side = findingSide(finding)
  return {
    start: finding.start_line ?? finding.end_line,
    end: finding.end_line,
    side,
    endSide: side,
  }
}

export function findingClipboardText(finding: ReviewFinding): string {
  const style = GROUP_STYLES[finding.group]
  const lines = [
    `**${style.label}: ${finding.title}**`,
    `${findingAnchorLabel(finding)}`,
    "",
    finding.description,
  ]
  if (finding.suggestion)
    lines.push("", "```suggestion", finding.suggestion, "```")
  return lines.join("\n")
}

// Inline findings live inside Pierre's diff via React portals, so their
// expand/collapse state is lifted to the review body and shared through context —
// surviving the annotation's mount/unmount as rows window in and out under
// virtualization, and letting the side panel drive the same expansion.
export interface ExpandedFindingContextValue {
  expandedId: string | null
  reviewUrl: string
  toggle: (finding: ReviewFinding) => void
  registerAnnotation: (id: string, node: HTMLElement | null) => void
}

export const ExpandedFindingContext =
  createContext<ExpandedFindingContextValue | null>(null)

export function useExpandedFinding(): ExpandedFindingContextValue {
  const ctx = useContext(ExpandedFindingContext)
  if (!ctx)
    throw new Error("useExpandedFinding must be used within its provider")
  return ctx
}
