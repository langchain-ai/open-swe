import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { agentsApi } from "@/features/agents/lib/api"
import type {
  ThreadFeedbackRating,
  ThreadFeedbackSubmission,
} from "@/features/agents/lib/api"

const ratings: Array<{ value: ThreadFeedbackRating; label: string }> = [
  { value: "bad", label: "💩 Bad" },
  { value: "good", label: "🙂 Good" },
  { value: "other", label: "💬 Other" },
]

export function ThreadFeedbackCard({
  threadId,
  login,
}: {
  threadId: string
  login: string | null
}) {
  const mutation = useMutation({
    mutationFn: (value: ThreadFeedbackSubmission) =>
      agentsApi.submitThreadFeedback(threadId, value),
  })
  const query = useQuery({
    queryKey: ["thread-feedback", threadId, login],
    queryFn: () => agentsApi.getThreadFeedback(threadId),
    enabled: Boolean(login),
    staleTime: 0,
    refetchInterval: (current) => {
      const status = mutation.data?.status ?? current.state.data?.status
      return status === "completed" || status === "dismissed" ? false : 30_000
    },
    retry: false,
  })
  const [rating, setRating] = useState<ThreadFeedbackRating | null>(null)
  const [comment, setComment] = useState("")
  const feedback =
    mutation.data ??
    (query.isFetchedAfterMount && !query.isError ? query.data : undefined)

  if (
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
      aria-label="Thread feedback"
      className="mt-4 space-y-3 rounded-xl border border-border bg-muted/30 p-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (!canSubmit || mutation.isPending || !rating) return
        mutation.mutate({ rating, comment: comment.trim() })
      }}
    >
      <p className="text-sm font-medium">How did Open SWE do?</p>
      <div className="flex gap-2" role="group" aria-label="Rating">
        {ratings.map((option) => (
          <Button
            key={option.value}
            type="button"
            variant={rating === option.value ? "secondary" : "outline"}
            size="sm"
            aria-pressed={rating === option.value}
            disabled={mutation.isPending}
            onClick={() => setRating(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
      <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
        Comment ({rating === "other" ? "required" : "optional"})
        <Textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          required={rating === "other"}
          maxLength={3000}
          disabled={mutation.isPending}
          placeholder="What worked well? What could be better?"
          className="min-h-20 text-sm"
        />
      </label>
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
          onClick={() => mutation.mutate({ action: "dismiss" })}
        >
          Dismiss
        </Button>
      </div>
    </form>
  )
}
