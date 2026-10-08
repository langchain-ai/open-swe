import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ThumbsDown, ThumbsUp } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useEffect, useState } from "react"

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
      "border-success bg-success text-success-secondary hover:bg-success hover:text-success-secondary dark:bg-success",
  },
  {
    value: "bad",
    label: "Bad",
    icon: ThumbsDown,
    className:
      "border-error bg-error text-error-secondary hover:bg-error hover:text-error-secondary dark:bg-error",
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
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [optimisticDismissed, setOptimisticDismissed] = useState(false)
  const mutation = useMutation({
    mutationFn: (value: ThreadFeedbackSubmission) =>
      agentsApi.submitThreadFeedback(threadId, value),
    meta: { errorTitle: "Couldn't save feedback" },
    onMutate: (value) => {
      if (value.action === "dismiss") {
        setOptimisticDismissed(true)
      } else if ("rating" in value) {
        setShowComment(value.rating === "bad")
        setShowConfirmation(value.rating !== "bad")
      } else {
        setShowComment(false)
        setShowConfirmation(true)
      }
    },
    onError: (_error, value) => {
      if (value.action === "dismiss") {
        setOptimisticDismissed(false)
      } else if ("rating" in value) {
        setShowComment(false)
        setShowConfirmation(false)
      } else {
        setShowComment(true)
        setShowConfirmation(false)
      }
    },
    onSuccess: (data, value) => {
      queryClient.setQueryData(["thread-feedback", threadId, login], data)
      if (value.action === "dismiss") return
      const shouldShowComment =
        "rating" in value &&
        value.rating === "bad" &&
        data.status === "completed" &&
        data.rating === "bad" &&
        !data.comment
      setShowComment(shouldShowComment)
      if (!shouldShowComment && data.status === "completed") {
        setShowConfirmation(true)
      }
    },
  })
  useEffect(() => {
    if (!showConfirmation || mutation.isPending) return
    const timeout = window.setTimeout(() => setShowConfirmation(false), 5000)
    return () => window.clearTimeout(timeout)
  }, [showConfirmation, mutation.isPending])
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
    optimisticDismissed ||
    (feedback?.status !== "ready" && feedback?.status !== "completed")
  ) {
    return null
  }

  if (feedback.status === "completed" && !showComment && !showConfirmation) {
    return null
  }

  if (showConfirmation) {
    return (
      <div
        role="status"
        className="mt-4 rounded-lg bg-surface-level-1 px-4 py-3 text-sm text-secondary"
      >
        Thanks for your feedback.
      </div>
    )
  }

  return (
    <form
      aria-label="Thread feedback"
      className={cn(
        "mt-4 rounded-lg bg-surface-level-1 p-4",
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
        <label className="flex flex-col gap-1.5 text-xs text-secondary">
          Comment (optional)
          <Textarea
            autoFocus
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={3000}
            disabled={mutation.isPending}
            placeholder="What could be better?"
            className="min-h-20 border-strong bg-surface-level-1 text-sm"
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
            className="text-secondary"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ action: "dismiss" })}
          >
            Dismiss
          </Button>
        </div>
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
            onClick={() => {
              setShowComment(false)
              setShowConfirmation(true)
            }}
          >
            Skip
          </Button>
        </div>
      )}
    </form>
  )
}
