import { CheckIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ThumbsDownIcon } from "@phosphor-icons/react/dist/ssr/ThumbsDown"
import { ThumbsUpIcon } from "@phosphor-icons/react/dist/ssr/ThumbsUp"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type {
  PublishedReviewAssessment,
  ReviewAssessmentFeedbackInput,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

interface Props {
  assessment: PublishedReviewAssessment
  owner: string
  repo: string
  number: number
  headSha: string
}

export function ReviewAssessmentCard(props: Props) {
  const { data: session } = useSession()
  return (
    <AssessmentCard
      key={`${props.assessment.review_id}:${session?.login}`}
      {...props}
      login={session?.login}
    />
  )
}

function AssessmentCard({
  assessment,
  owner,
  repo,
  number,
  headSha,
  login,
}: Props & { login?: string }) {
  const queryClient = useQueryClient()
  const queryKey = [
    "assessment-feedback",
    owner,
    repo,
    number,
    assessment.review_id,
    login,
  ]
  const feedback = useQuery({
    queryKey,
    queryFn: () =>
      api.getAssessmentFeedback(owner, repo, number, assessment.review_id),
    enabled: Boolean(login),
  })
  const [editing, setEditing] = useState(
    () =>
      typeof window !== "undefined" &&
      window.location.hash === "#assessment-feedback"
  )
  const [draft, setDraft] = useState<{
    rating?: ReviewAssessmentFeedbackInput["rating"]
    comment: string
  } | null>(null)
  const value = draft ?? feedback.data
  const save = useMutation({
    mutationFn: (input: ReviewAssessmentFeedbackInput) =>
      api.saveAssessmentFeedback(
        owner,
        repo,
        number,
        assessment.review_id,
        input
      ),
    meta: { errorTitle: "Couldn't save feedback" },
    onSuccess: async (saved) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData(queryKey, saved)
      setDraft(null)
      setEditing(false)
    },
  })

  return (
    <section
      id="assessment-feedback"
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
                : "Needs human review"}
          </span>
          <span
            className="text-xs text-secondary"
            title={
              assessment.approved
                ? "Open SWE approved this pull request on GitHub."
                : assessment.dry_run
                  ? "Auto-approval is in dry run for this repository: Open SWE scores the risk but never approves on GitHub."
                  : "Open SWE only advises here; it doesn't approve on GitHub."
            }
          >
            {assessment.approved
              ? "Automatic approval"
              : assessment.dry_run
                ? "Dry run"
                : "Advisory"}
          </span>
        </div>
        {!editing && login && feedback.isSuccess && (
          <div className="flex items-center gap-space-2">
            {feedback.data && (
              <span
                role="status"
                className="flex items-center gap-space-1 text-xs text-secondary"
              >
                <CheckIcon weight="regular" className="size-3.5" /> Feedback
                saved
              </span>
            )}
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              onClick={() => {
                save.reset()
                setEditing(true)
              }}
            >
              {feedback.data ? "Edit feedback" : "Rate assessment"}
            </Button>
          </div>
        )}
      </div>
      <details className="mt-space-2 text-xs text-secondary">
        <summary className="cursor-pointer">Why this assessment?</summary>
        <p className="mt-space-2 text-sm whitespace-pre-wrap text-primary">
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
      {feedback.isError && (
        <p role="alert" className="mt-space-3 text-xs text-error-secondary">
          Could not load your feedback.{" "}
          <Button
            size="xs"
            variant="underlined"
            color="secondary"
            onClick={() => void feedback.refetch()}
          >
            Retry
          </Button>
        </p>
      )}
      {editing && login && feedback.isSuccess && (
        <form
          className="mt-space-3 space-y-space-3 border-t border-default pt-space-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (value?.rating && !save.isPending)
              save.mutate({ rating: value.rating, comment: value.comment })
          }}
        >
          <fieldset disabled={save.isPending} className="space-y-space-3">
            <legend className="mb-space-2 text-xs font-medium">
              Was this assessment helpful?
            </legend>
            <div className="flex gap-space-2">
              {(["helpful", "unhelpful"] as const).map((rating) => (
                <Button
                  key={rating}
                  size="xs"
                  color="secondary"
                  variant={value?.rating === rating ? "normal" : "outlined"}
                  aria-pressed={value?.rating === rating}
                  leftDecorator={
                    rating === "helpful" ? ThumbsUpIcon : ThumbsDownIcon
                  }
                  onClick={() =>
                    setDraft({ rating, comment: value?.comment ?? "" })
                  }
                >
                  {rating === "helpful" ? "Helpful" : "Not helpful"}
                </Button>
              ))}
            </div>
            <Textarea
              size="md"
              label="Comment (optional)"
              maxLength={3000}
              value={value?.comment ?? ""}
              onChange={(comment) =>
                setDraft({ rating: value?.rating, comment })
              }
              placeholder="What was right, or what did we miss?"
            />
            <p className="text-xs text-secondary">
              Saved in Open SWE. Your comment is not posted to GitHub.
            </p>
            <div className="flex gap-space-2">
              <Button type="submit" size="xs" disabled={!value?.rating}>
                {save.isPending ? "Saving…" : "Save feedback"}
              </Button>
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => {
                  setEditing(false)
                  setDraft(null)
                  save.reset()
                }}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  )
}
