import { useId, useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { ThreadFeedbackRating } from "@/features/agents/lib/api"
import { useThreadFeedback } from "@/features/agents/lib/threadFeedback"

const ratings: Array<{ value: ThreadFeedbackRating; label: string }> = [
  { value: "bad", label: "💩 Bad" },
  { value: "good", label: "🙂 Good" },
  { value: "other", label: "💬 Other" },
]

export function ThreadFeedbackCard({
  threadId,
  login,
  isActive,
}: {
  threadId: string
  login: string | null
  isActive: boolean
}) {
  const { query, mutation } = useThreadFeedback(threadId, login, isActive)
  const [rating, setRating] = useState<ThreadFeedbackRating | null>(null)
  const [comment, setComment] = useState("")
  const [showComment, setShowComment] = useState(false)
  const commentId = useId()
  const titleId = useId()
  const feedback = query.data

  if (
    isActive ||
    !login ||
    (feedback?.status !== "ready" && feedback?.status !== "completed")
  ) {
    return null
  }

  if (feedback.status === "completed") {
    return (
      <div
        role="status"
        className="mt-4 rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
      >
        Thanks for your feedback.
      </div>
    )
  }

  const canSubmit =
    rating !== null && (rating !== "other" || comment.trim().length > 0)
  return (
    <form
      aria-labelledby={titleId}
      className="mt-4 space-y-3 rounded-xl border border-border bg-muted/30 p-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (!canSubmit || mutation.isPending || !rating) return
        mutation.mutate({ rating, comment: comment.trim() })
      }}
    >
      <p id={titleId} className="text-sm font-medium">
        How did Open SWE do?
      </p>
      <div className="flex gap-2" role="group" aria-label="Rating">
        {ratings.map((option) => (
          <Button
            key={option.value}
            type="button"
            variant={rating === option.value ? "secondary" : "outline"}
            size="sm"
            aria-pressed={rating === option.value}
            disabled={mutation.isPending}
            onClick={() => {
              setRating(option.value)
              if (option.value === "other") setShowComment(true)
            }}
          >
            {option.label}
          </Button>
        ))}
      </div>
      {showComment || rating === "other" ? (
        <div className="space-y-1.5">
          <label htmlFor={commentId} className="text-xs text-muted-foreground">
            Comment ({rating === "other" ? "required" : "optional"})
          </label>
          <Textarea
            id={commentId}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            required={rating === "other"}
            maxLength={3000}
            disabled={mutation.isPending}
            placeholder="What worked well? What could be better?"
            className="min-h-20 text-sm"
          />
        </div>
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setShowComment(true)}
          disabled={mutation.isPending}
        >
          Add a comment
        </Button>
      )}
      {mutation.isError && (
        <p role="alert" className="text-xs text-destructive">
          Your feedback could not be saved. Please try again.
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={!canSubmit || mutation.isPending}
        >
          {mutation.isPending ? "Saving…" : "Submit feedback"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate("dismiss")}
        >
          Dismiss
        </Button>
      </div>
    </form>
  )
}
