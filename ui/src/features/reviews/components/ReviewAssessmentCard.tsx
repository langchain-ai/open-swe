import type { PublishedReviewAssessment } from "@/lib/api"

interface Props {
  assessment: PublishedReviewAssessment
  headSha: string
}

export function ReviewAssessmentCard({ assessment, headSha }: Props) {
  return (
    <section
      aria-label="Review assessment"
      className="mt-space-4 rounded-lg border border-default bg-surface-level-1 p-space-4 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <div className="flex flex-wrap items-center gap-space-2">
          <span className="font-medium">Risk {assessment.risk_score}/5</span>
          <span className="text-secondary">·</span>
          <span>
            {assessment.approved
              ? "Approved"
              : assessment.decision === "would_approve"
                ? "Would approve"
                : assessment.decision === "would_approve_conditionally"
                  ? "Needs approval of one change"
                  : "Needs human review"}
          </span>
          <span
            className="text-xs text-secondary"
            title={
              assessment.approved
                ? "Robin approved this pull request on GitHub."
                : assessment.dry_run
                  ? "Auto-approval is in dry run for this repository: Robin scores the risk but never approves on GitHub."
                  : "Robin only advises here; it doesn't approve on GitHub."
            }
          >
            {assessment.approved
              ? "Automatic approval"
              : assessment.dry_run
                ? "Dry run"
                : "Advisory"}
          </span>
        </div>
      </div>
      {assessment.approval_condition && (
        <p className="mt-space-2">
          <span className="font-medium">Needs a person to approve:</span>{" "}
          {assessment.approval_condition}
        </p>
      )}
      {assessment.auto_approved && (
        <p className="mt-space-1 text-secondary">
          <span className="font-medium">Covered by auto-approve:</span>{" "}
          {assessment.auto_approved}
        </p>
      )}
      <details className="mt-space-2 text-xs text-secondary">
        <summary className="cursor-pointer">Why this assessment?</summary>
        <p
          data-reply-part="Robin's risk assessment"
          className="mt-space-2 text-sm whitespace-pre-wrap text-primary"
        >
          {assessment.explanation}
        </p>
        <p className="mt-space-2">
          Reviewed commit {assessment.head_sha.slice(0, 7)}. Risk ranges from 1
          (low) to 5 (high).
        </p>
      </details>
      {headSha !== assessment.head_sha && (
        <p className="mt-space-2 text-xs text-warning-secondary">
          This assessment is for an earlier commit.
        </p>
      )}
    </section>
  )
}
