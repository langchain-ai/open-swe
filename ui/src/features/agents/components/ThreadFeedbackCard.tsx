import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ThumbsDown, ThumbsUp } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { agentsApi } from "@/features/agents/lib/api"
import type {
  ThreadFeedbackRating,
  ThreadFeedbackSubmission,
} from "@/features/agents/lib/api"
import { cn } from "@/lib/utils"

const ratings: Array<{
  value: ThreadFeedbackRating
  label: string
  icon: LucideIcon
  className: string
}> = [
  {
    value: "good",
    label: "Good",
    icon: ThumbsUp,
    className:
      "border-success/40 bg-success/10 text-success-foreground hover:bg-success/20 hover:text-success-foreground dark:bg-success/10",
  },
  {
    value: "bad",
    label: "Bad",
    icon: ThumbsDown,
    className:
      "border-destructive/40 bg-destructive/10 text-destructive-foreground hover:bg-destructive/20 hover:text-destructive-foreground dark:bg-destructive/10",
  },
]

export function ThreadFeedbackCard({
  threadId,
  login,
}: {
  threadId: string
  login: string | null
}) {
  const queryClient = useQueryClient()
  const [showComment, setShowComment] = useState(false)
  const [comment, setComment] = useState("")
  const mutation = useMutation({
    mutationFn: (value: ThreadFeedbackSubmission) =>
      agentsApi.submitThreadFeedback(threadId, value),
    onSuccess: (data, value) => {
      queryClient.setQueryData(["thread-feedback", threadId, login], data)
      setShowComment(
        "rating" in value &&
          value.rating === "bad" &&
          data.status === "completed" &&
          data.rating === "bad" &&
          !data.comment
      )
    },
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
  const feedback =
    mutation.data ??
    (query.isFetchedAfterMount && !query.isError ? query.data : undefined)

  if (
    !login ||
    (feedback?.status !== "ready" && feedback?.status !== "completed")
  ) {
    return null
  }

  if (feedback.status === "completed" && !showComment) {
    return (
      <div
        role="status"
        className="mt-4 rounded-lg bg-card px-4 py-3 text-sm text-muted-foreground"
      >
        Thanks for your feedback.
      </div>
    )
  }

  return (
    <form
      aria-label="Thread feedback"
      className={cn(
        "mt-4 rounded-lg bg-card p-4",
        showComment
          ? "space-y-3"
          : "flex flex-wrap items-center justify-between gap-x-6 gap-y-3"
      )}
      onSubmit={(event) => {
        event.preventDefault()
        if (!showComment || mutation.isPending || !comment.trim()) return
        mutation.mutate({ action: "comment", comment: comment.trim() })
      }}
    >
      <p className="text-sm font-medium">
        {showComment ? "How could Open SWE do better?" : "How did Open SWE do?"}
      </p>
      {showComment ? (
        <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
          Comment (optional)
          <Textarea
            autoFocus
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={3000}
            disabled={mutation.isPending}
            placeholder="What could be better?"
            className="min-h-20 border-foreground/20 bg-background text-sm"
          />
        </label>
      ) : (
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Rating"
        >
          {ratings.map((option) => (
            <Button
              key={option.value}
              type="button"
              variant="outline"
              size="lg"
              className={cn("px-3 text-sm", option.className)}
              disabled={mutation.isPending}
              onClick={() => mutation.mutate({ rating: option.value })}
            >
              <option.icon aria-hidden="true" className="size-4" />
              {option.label}
            </Button>
          ))}
          <Button
            type="button"
            size="lg"
            variant="ghost"
            className="text-muted-foreground"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ action: "dismiss" })}
          >
            Dismiss
          </Button>
        </div>
      )}
      {mutation.isError && (
        <p role="alert" className="w-full text-xs text-destructive">
          Your feedback could not be saved. Please try again.
        </p>
      )}
      {showComment && (
        <div className="flex items-center gap-2">
          <Button
            type="submit"
            size="sm"
            disabled={!comment.trim() || mutation.isPending}
          >
            {mutation.isPending ? "Saving…" : "Submit comment"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={mutation.isPending}
            onClick={() => setShowComment(false)}
          >
            Skip
          </Button>
        </div>
      )}
    </form>
  )
}
