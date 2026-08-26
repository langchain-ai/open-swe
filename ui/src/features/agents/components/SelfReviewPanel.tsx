import { ExternalLinkIcon } from "lucide-react"

import type {
  SelfReview,
  SelfReviewDisposition,
  SelfReviewFinding,
  SelfReviewSeverity,
} from "@/features/agents/lib/types"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { useThreadSelfReview } from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"

const SEVERITY_STYLES: Record<SelfReviewSeverity, string> = {
  critical: "bg-destructive/15 text-destructive",
  high: "bg-destructive/10 text-destructive",
  medium: "bg-warning/15 text-warning-foreground",
  low: "bg-muted text-muted-foreground",
}

const DISPOSITION_LABELS: Record<SelfReviewDisposition, string> = {
  pending: "Not addressed",
  fixed: "Fixed in this PR",
  deferred: "Needs your call",
  dismissed: "Dismissed",
}

const DISPOSITION_STYLES: Record<SelfReviewDisposition, string> = {
  pending: "bg-muted text-muted-foreground",
  fixed: "bg-success/15 text-success-foreground",
  deferred: "bg-warning/15 text-warning-foreground",
  dismissed: "bg-muted text-muted-foreground",
}

export function findingAnchorLabel(finding: SelfReviewFinding): string {
  if (!finding.file) return ""
  if (finding.start_line === null) return finding.file
  if (finding.end_line === null || finding.end_line === finding.start_line)
    return `${finding.file}:${finding.start_line}`
  return `${finding.file}:${finding.start_line}-${finding.end_line}`
}

/** The one-line summary the transcript card and the panel header both show. */
export function selfReviewSummary(review: SelfReview): string {
  const total = review.findings.length
  if (total === 0) return "No findings"
  const counts = review.findings.reduce<Record<string, number>>(
    (acc, finding) => {
      acc[finding.disposition] = (acc[finding.disposition] ?? 0) + 1
      return acc
    },
    {}
  )
  const parts = [`${total} finding${total === 1 ? "" : "s"}`]
  if (counts.fixed) parts.push(`${counts.fixed} fixed`)
  if (counts.deferred) parts.push(`${counts.deferred} needs your call`)
  if (counts.dismissed) parts.push(`${counts.dismissed} dismissed`)
  if (counts.pending) parts.push(`${counts.pending} open`)
  return parts.join(" · ")
}

function Badge(props: { className: string; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-md px-1.5 py-0.5 text-[11px] font-medium",
        props.className
      )}
    >
      {props.children}
    </span>
  )
}

function FindingRow({
  finding,
  onOpenFile,
}: {
  finding: SelfReviewFinding
  onOpenFile?: (path: string, line: number | null) => void
}) {
  const anchor = findingAnchorLabel(finding)
  return (
    <li className="border-b border-border px-3 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge className={SEVERITY_STYLES[finding.severity]}>
          {finding.severity}
        </Badge>
        <Badge className={DISPOSITION_STYLES[finding.disposition]}>
          {DISPOSITION_LABELS[finding.disposition]}
        </Badge>
        {finding.category && (
          <span className="text-[11px] text-muted-foreground">
            {finding.category}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm font-medium">{finding.title}</p>
      {anchor && (
        <button
          type="button"
          disabled={!onOpenFile}
          onClick={() => onOpenFile?.(finding.file, finding.start_line)}
          className="mt-0.5 block max-w-full truncate font-mono text-xs text-muted-foreground hover:text-foreground disabled:hover:text-muted-foreground"
        >
          {anchor}
        </button>
      )}
      {finding.description && (
        <div className="mt-2 text-sm text-muted-foreground">
          <Markdown content={finding.description} />
        </div>
      )}
      {finding.disposition_note && (
        <p className="mt-2 border-l-2 border-border pl-2 text-xs text-muted-foreground">
          {finding.disposition_note}
        </p>
      )}
    </li>
  )
}

/**
 * The Review surface: what the agent found in the PR it wrote here.
 *
 * These findings are deliberately absent from the PR itself — the agent reviews
 * its own work in the run that wrote it, so this panel is where they land.
 */
export function SelfReviewPanel({
  threadId,
  onOpenFile,
}: {
  threadId: string
  onOpenFile?: (path: string, line: number | null) => void
}) {
  const query = useThreadSelfReview(threadId)
  const reviews = query.data?.reviews ?? []

  if (query.isPending) {
    return (
      <div className="p-4 text-sm text-muted-foreground">Loading review…</div>
    )
  }
  if (reviews.length === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        This thread has not self-reviewed a pull request yet.
      </div>
    )
  }

  return (
    <ScrollArea className="min-h-0 flex-1" data-testid="self-review-panel">
      {reviews.map((review) => (
        <section key={review.prNumber} className="border-b border-border">
          <header className="flex items-center justify-between gap-2 px-3 py-2">
            <div className="min-w-0">
              <a
                href={review.prUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-sm font-medium hover:underline"
              >
                PR #{review.prNumber}
                <ExternalLinkIcon className="size-3" />
              </a>
              <p className="truncate text-xs text-muted-foreground">
                {selfReviewSummary(review)}
              </p>
            </div>
          </header>
          <ul>
            {review.findings.map((finding) => (
              <FindingRow
                key={finding.id}
                finding={finding}
                {...(onOpenFile ? { onOpenFile } : {})}
              />
            ))}
          </ul>
          {review.findings.length === 0 && (
            <p className="px-3 pb-3 text-sm text-muted-foreground">
              The self-review found nothing that met the bar.
            </p>
          )}
        </section>
      ))}
    </ScrollArea>
  )
}
