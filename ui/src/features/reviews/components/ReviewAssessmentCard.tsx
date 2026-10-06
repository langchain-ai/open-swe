import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CheckIcon, ThumbsDownIcon, ThumbsUpIcon } from "@phosphor-icons/react"
import type {
  PublishedReviewAssessment,
  ReviewAssessmentFeedbackInput,
} from "@/lib/api"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

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
      className="mt-4 rounded-compact border border-line bg-panel p-4 text-body"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">Risk {assessment.risk_score}/5</span>
          <span className="text-ink-subtle">·</span>
          <span>
            {assessment.approved
              ? "Approved"
              : assessment.decision === "would_approve"
                ? "Would approve"
                : "Needs human review"}
          </span>
          <span className="text-meta text-ink-subtle">
            {assessment.approved
              ? "Automatic approval"
              : assessment.dry_run
                ? "Dry run"
                : "Advisory"}
          </span>
        </div>
        {!editing && login && feedback.isSuccess && (
          <div className="flex items-center gap-2">
            {feedback.data && (
              <span
                role="status"
                className="flex items-center gap-1 text-meta text-ink-subtle"
              >
                <CheckIcon /> Feedback saved
              </span>
            )}
            <Button
              size="compact"
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
      <details className="mt-2 text-meta text-ink-subtle">
        <summary className="cursor-pointer">Why this assessment?</summary>
        <p className="mt-2 text-body whitespace-pre-wrap text-ink">
          {assessment.explanation}
        </p>
        <p className="mt-2">
          Reviewed commit {assessment.head_sha.slice(0, 7)}. Risk ranges from 1
          (low) to 5 (high).
        </p>
      </details>
      {headSha !== assessment.head_sha && (
        <p className="mt-2 text-label text-attention">
          This assessment is for an earlier commit.
        </p>
      )}
      {feedback.isError && (
        <p role="alert" className="mt-3 text-label text-risk">
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
          className="mt-3 space-y-3 border-t border-line pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (value?.rating && !save.isPending)
              save.mutate({ rating: value.rating, comment: value.comment })
          }}
        >
          <fieldset disabled={save.isPending} className="space-y-3">
            <legend className="mb-2 text-label font-medium">
              Was this assessment helpful?
            </legend>
            <div className="flex gap-2">
              {(["helpful", "unhelpful"] as const).map((rating) => (
                <Button
                  key={rating}
                  type="button"
                  size="compact"
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
            <label className="block space-y-1.5 text-meta text-ink-subtle">
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
                className="min-h-20 text-body"
              />
            </label>
            <p className="text-meta text-ink-subtle">
              Saved in Open SWE. Your comment is not posted to GitHub.
            </p>
            <div className="flex gap-2">
              <Button type="submit" size="compact" disabled={!value?.rating}>
                {save.isPending ? "Saving…" : "Save feedback"}
              </Button>
              <Button
                type="button"
                size="compact"
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
        </form>
      )}
    </section>
  )
}
