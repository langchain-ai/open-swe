import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useLocation } from "@tanstack/react-router"
import { useId, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { currentAuthRedirectPath } from "@/lib/auth-redirect"
import {
  api,
  isGithubReauthError,
  loginUrl,
  type ReviewApprovalFeedback,
  type ReviewRiskAssessment,
  type ReviewRiskFeedback,
  type ReviewRiskResponse,
} from "@/lib/api"

interface ReviewRiskCardProps {
  owner: string
  repo: string
  number: number
  headSha: string
}

export function ReviewRiskCard(props: ReviewRiskCardProps) {
  const { hash } = useLocation()
  const assessmentId = /^#?review-risk-([0-9a-f-]{36})$/.exec(hash)?.[1]
  const queryKey = [
    "review-risk",
    props.owner,
    props.repo,
    props.number,
    props.headSha,
    assessmentId,
  ]
  const query = useQuery({
    queryKey,
    queryFn: () =>
      api.getReviewRisk(props.owner, props.repo, props.number, assessmentId),
    refetchInterval: 15_000,
  })
  if (query.isError && !query.data?.assessment) {
    return (
      <div
        role="alert"
        className="flex items-center gap-3 border-b border-border px-4 py-3 text-xs"
      >
        <span className="text-muted-foreground">
          {isGithubReauthError(query.error)
            ? "Sign in to view this assessment and leave feedback."
            : "Could not load the review assessment."}
        </span>
        {isGithubReauthError(query.error) ? (
          <a
            className="font-medium text-primary underline underline-offset-4"
            href={loginUrl(currentAuthRedirectPath())}
          >
            Sign in with GitHub
          </a>
        ) : (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            Try again
          </Button>
        )}
      </div>
    )
  }
  const record = query.data?.assessment
  if (!record) return null
  return (
    <RiskFeedbackForm
      key={record.id}
      {...props}
      record={record}
      feedback={query.data?.feedback ?? null}
      reactions={query.data?.reactions}
      queryKey={queryKey}
      initiallyOpen={Boolean(assessmentId)}
    />
  )
}

function RiskFeedbackForm({
  owner,
  repo,
  number,
  headSha,
  record,
  feedback,
  reactions,
  queryKey,
  initiallyOpen,
}: ReviewRiskCardProps & {
  record: ReviewRiskAssessment
  feedback: ReviewRiskFeedback | null
  reactions: ReviewRiskResponse["reactions"]
  queryKey: readonly (string | number | undefined)[]
  initiallyOpen: boolean
}) {
  const commentId = useId()
  const summaryRef = useRef<HTMLElement>(null)
  const [open, setOpen] = useState(initiallyOpen)
  const evaluation = record.approval_evaluation
  const approvalLabel =
    evaluation &&
    {
      would_approve: "Would approve",
      needs_human_review: "Needs human review",
      insufficient_evidence: "Insufficient evidence",
    }[evaluation.decision]
  const [decision, setDecision] = useState<ReviewApprovalFeedback | null>(
    feedback?.decision ?? null
  )
  const [comment, setComment] = useState(feedback?.comment ?? "")
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (submission: {
      decision: ReviewApprovalFeedback | null
      comment: string
    }) =>
      api.submitReviewRiskFeedback(owner, repo, number, record.id, submission),
    onSuccess: () => {
      setOpen(false)
      summaryRef.current?.focus()
      void queryClient.invalidateQueries({ queryKey })
    },
  })
  return (
    <details
      id={`review-risk-${record.id}`}
      open={open}
      onToggle={(event) => {
        if (event.target === event.currentTarget) {
          setOpen(event.currentTarget.open)
        }
      }}
      className="group/assessment max-h-[60vh] shrink-0 overflow-y-auto border-b border-border bg-background px-4 py-3 text-xs"
    >
      <summary
        ref={summaryRef}
        className="flex cursor-pointer list-none flex-wrap items-center gap-2 [&::-webkit-details-marker]:hidden"
      >
        <span className="font-medium">
          {evaluation ? `Approval: ${approvalLabel}` : "PR risk"}
        </span>
        <Badge variant="outline">
          {record.score === null ? "Not assessed" : `${record.score}/5 risk`}
        </Badge>
        {headSha !== record.head_sha && (
          <Badge variant="secondary">Earlier commit</Badge>
        )}
        <span className="text-muted-foreground">
          {record.confidence} confidence
        </span>
        {record.github_review_id && (
          <a
            className="text-muted-foreground underline underline-offset-4 hover:text-foreground"
            href={`https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pull/${number}#pullrequestreview-${record.github_review_id}`}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            {reactions?.synced_at
              ? `👍 ${reactions.helpful} · 👎 ${reactions.unhelpful} · React on GitHub`
              : "👍 / 👎 on GitHub"}
          </a>
        )}
        {save.isSuccess && (
          <span role="status" className="text-muted-foreground">
            Feedback saved. Thank you.
          </span>
        )}
        <span className="ml-auto font-medium text-primary group-open/assessment:hidden">
          Give feedback ↓
        </span>
        <span className="ml-auto hidden font-medium text-muted-foreground group-open/assessment:inline">
          Hide feedback ↑
        </span>
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-muted-foreground">
          Commit <code>{record.head_sha.slice(0, 12)}</code> ·{" "}
          {record.open_findings} open findings · Advisory only
        </p>
        {headSha !== record.head_sha && (
          <p className="rounded-md bg-muted px-3 py-2">
            This assessment is for an earlier commit. Your feedback applies to
            that commit.
          </p>
        )}
        <p className="text-muted-foreground">
          React on GitHub: 👍 useful · 👎 unhelpful. You can add an explanation
          or suggest a different approval decision below. Neither is required.
        </p>
        {reactions?.viewer_rating && (
          <p className="text-muted-foreground">
            {reactions.viewer_rating === "conflicting"
              ? "You left both 👍 and 👎; remove one on GitHub for your vote to count."
              : `Your GitHub feedback: ${reactions.viewer_rating === "helpful" ? "👍 useful" : "👎 unhelpful"}.`}
          </p>
        )}
        {reactions?.synced_at && (
          <p className="text-muted-foreground">
            Reactions last synced{" "}
            {new Date(reactions.synced_at).toLocaleString()}.
          </p>
        )}
        <form
          className="space-y-3 rounded-lg border border-border bg-muted/20 p-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (decision || comment.trim()) save.mutate({ decision, comment })
          }}
        >
          <fieldset disabled={save.isPending} className="space-y-2">
            <legend className="mb-2 font-medium">
              {evaluation
                ? "What should the approval decision have been?"
                : "Could this commit have skipped human review?"}
            </legend>
            <div className="flex flex-wrap gap-2">
              {(
                [
                  [
                    "safe",
                    evaluation ? "Would approve" : "Could skip human review",
                  ],
                  ["needs_review", "Needs human review"],
                  ["unsure", evaluation ? "Insufficient evidence" : "Unsure"],
                ] as const
              ).map(([value, label]) => (
                <Button
                  key={value}
                  type="button"
                  aria-pressed={decision === value}
                  variant={decision === value ? "default" : "outline"}
                  size="lg"
                  onClick={() => {
                    setDecision(decision === value ? null : value)
                    save.reset()
                  }}
                >
                  {label}
                </Button>
              ))}
            </div>
            <label className="block" htmlFor={commentId}>
              Why? (optional)
            </label>
            <Textarea
              id={commentId}
              value={comment}
              maxLength={3000}
              rows={2}
              placeholder="What influenced your decision?"
              onChange={(event) => {
                setComment(event.target.value)
                save.reset()
              }}
            />
          </fieldset>
          <Button
            type="submit"
            disabled={(!decision && !comment.trim()) || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save feedback"}
          </Button>
          {save.isError && (
            <p role="alert">
              {isGithubReauthError(save.error) ? (
                <>
                  Your session expired. Your draft is still here.{" "}
                  <a
                    className="font-medium text-primary underline underline-offset-4"
                    href={loginUrl(currentAuthRedirectPath())}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Sign in with GitHub
                  </a>
                  , then save again.
                </>
              ) : (
                "Could not save feedback. Please try again."
              )}
            </p>
          )}
        </form>
        <details className="border-t border-border pt-3 text-muted-foreground">
          <summary className="cursor-pointer font-medium">
            Why this decision?
          </summary>
          <div className="mt-3 space-y-3 leading-relaxed">
            {evaluation && (
              <section
                aria-label="Approval policy evaluation"
                className="space-y-3"
              >
                <p className="text-muted-foreground">
                  Shadow evaluation — no approval was granted.
                </p>
                {evaluation.policy && (
                  <p>
                    Policy: {evaluation.policy.source} · Version{" "}
                    <code>{evaluation.policy.version.slice(0, 12)}</code> · Base{" "}
                    <code>{evaluation.policy.base_sha.slice(0, 12)}</code>
                  </p>
                )}
                <ul className="space-y-2">
                  {evaluation.criteria.map((criterion) => (
                    <li key={criterion.id}>
                      <p className="font-medium">
                        {criterion.status.toUpperCase()} · {criterion.title}
                      </p>
                      <p>{criterion.evidence}</p>
                      {criterion.requirement && (
                        <details className="mt-1 text-muted-foreground">
                          <summary className="cursor-pointer">
                            Policy requirement
                          </summary>
                          <p className="whitespace-pre-wrap">
                            {criterion.requirement}
                          </p>
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <p>{record.rationale}</p>
            <p className="text-muted-foreground">
              Commit <code>{record.head_sha.slice(0, 12)}</code> ·{" "}
              {record.open_findings} open findings · 1 = lowest risk, 5 =
              highest
            </p>
            {record.score !== record.proposed_score && (
              <p>The score was raised to reflect unresolved review findings.</p>
            )}
            {record.limitations.length > 0 && (
              <p>Limitations: {record.limitations.join("; ")}</p>
            )}
            <p className="text-muted-foreground">
              Advisory only. Feedback helps calibrate future autoapproval; it
              does not approve or merge this PR.
            </p>
          </div>
        </details>
      </div>
    </details>
  )
}
