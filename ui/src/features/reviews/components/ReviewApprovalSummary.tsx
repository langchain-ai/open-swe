import type { ReviewApprovalAssessment } from "@/lib/api"
import { cn } from "@/lib/utils"

function label(value: string): string {
  return value.replaceAll("_", " ")
}

export function ReviewApprovalSummary({
  assessment,
}: {
  assessment: ReviewApprovalAssessment | null
}) {
  if (!assessment) return null

  const approved =
    !assessment.stale && assessment.github_review_event === "APPROVE"
  const title = assessment.stale
    ? "Assessment is for an earlier commit"
    : approved
      ? "Approved by Open SWE"
      : assessment.decision === "skipped_duplicate"
        ? "Existing Open SWE approval retained"
        : "Open SWE approval not submitted"

  return (
    <section
      className={cn(
        "mt-4 rounded-lg border p-4",
        approved
          ? "border-emerald-600/40 bg-emerald-500/5"
          : assessment.stale
            ? "border-amber-600/40 bg-amber-500/5"
            : "border-border bg-card"
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        <span className="rounded-full border border-border px-2 py-0.5 text-xs font-medium tabular-nums">
          {assessment.score == null
            ? "Score unavailable"
            : `${assessment.score}/100`}
        </span>
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        Rubric v{assessment.rubric_version} · commit{" "}
        {assessment.assessed_sha.slice(0, 7)}
        {assessment.policy.effective_threshold != null
          ? ` · threshold ${assessment.policy.effective_threshold}`
          : ""}
      </div>
      {assessment.reasons.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {assessment.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {assessment.risks.length > 0 && (
        <div className="mt-3 text-xs">
          <span className="font-medium">Remaining risks:</span>{" "}
          <span className="text-muted-foreground">
            {assessment.risks.join(" · ")}
          </span>
        </div>
      )}
      {assessment.blockers.length > 0 && (
        <div className="mt-3 text-xs">
          <span className="font-medium">Approval blockers:</span>{" "}
          <span className="text-muted-foreground">
            {assessment.blockers.map(label).join(" · ")}
          </span>
        </div>
      )}
    </section>
  )
}
