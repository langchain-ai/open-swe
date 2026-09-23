import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CheckIcon, ThumbsDownIcon, ThumbsUpIcon } from "@phosphor-icons/react"
import type {
  PublishedReviewAssessment,
  ReviewAssessmentFeedbackInput,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

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
      className="mt-4 rounded-lg border border-border bg-card p-4 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">Risk {assessment.risk_score}/5</span>
          <span className="text-muted-foreground">·</span>
          <span>
            {assessment.approved
              ? "Approved"
              : assessment.decision === "would_approve"
                ? "Would approve"
                : "Needs human review"}
          </span>
          <span className="text-xs text-muted-foreground">
            {assessment.approved ? "Automatic approval" : "Advisory"}
          </span>
        </div>
        {!editing && login && feedback.isSuccess && (
          <div className="flex items-center gap-2">
            {feedback.data && (
              <span
                role="status"
                className="flex items-center gap-1 text-xs text-muted-foreground"
              >
                <CheckIcon /> Feedback saved
              </span>
            )}
            <Button
              size="sm"
              variant="ghost"
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
      <details className="mt-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer">Why this assessment?</summary>
        <p className="mt-2 text-sm whitespace-pre-wrap text-foreground">
          {assessment.explanation}
        </p>
        <p className="mt-2">
          Reviewed commit {assessment.head_sha.slice(0, 7)}. Risk ranges from 1
          (low) to 5 (high).
        </p>
      </details>
      {headSha !== assessment.head_sha && (
        <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
          This assessment is for an earlier commit.
        </p>
      )}
      {feedback.isError && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          Could not load your feedback.{" "}
          <button
            type="button"
            className="underline"
            onClick={() => void feedback.refetch()}
          >
            Retry
          </button>
        </p>
      )}
      {editing && login && feedback.isSuccess && (
        <form
          className="mt-3 space-y-3 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (value?.rating && !save.isPending)
              save.mutate({ rating: value.rating, comment: value.comment })
          }}
        >
          <fieldset disabled={save.isPending} className="space-y-3">
            <legend className="mb-2 text-xs font-medium">
              Was this assessment helpful?
            </legend>
            <div className="flex gap-2">
              {(["helpful", "unhelpful"] as const).map((rating) => (
                <Button
                  key={rating}
                  type="button"
                  size="sm"
                  variant={value?.rating === rating ? "secondary" : "outline"}
                  aria-pressed={value?.rating === rating}
                  onClick={() =>
                    setDraft({ rating, comment: value?.comment ?? "" })
                  }
                >
                  {rating === "helpful" ? <ThumbsUpIcon /> : <ThumbsDownIcon />}
                  {rating === "helpful" ? "Helpful" : "Not helpful"}
                </Button>
              ))}
            </div>
            <label className="block space-y-1.5 text-xs text-muted-foreground">
              <span>Comment (optional)</span>
              <Textarea
                maxLength={3000}
                value={value?.comment ?? ""}
                onChange={(event) =>
                  setDraft({
                    rating: value?.rating,
                    comment: event.target.value,
                  })
                }
                placeholder="What was right, or what did we miss?"
                className="min-h-20 text-sm"
              />
            </label>
            <p className="text-xs text-muted-foreground">
              Saved in Open SWE. Your comment is not posted to GitHub.
            </p>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={!value?.rating}>
                {save.isPending ? "Saving…" : "Save feedback"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
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
          {save.isError && (
            <p role="alert" className="text-xs text-destructive">
              Could not save feedback. Your draft is still here; please try
              again.
            </p>
          )}
        </form>
      )}
    </section>
  )
}
