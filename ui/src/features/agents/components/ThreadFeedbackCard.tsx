import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { CheckCircle, ThumbsDown, ThumbsUp } from "@/components/glyphs"
import type { Glyph } from "@/components/glyphs"
import { agentsApi } from "@/features/agents/lib/api"
import type {
  ThreadFeedbackRating,
  ThreadFeedbackSubmission,
} from "@/features/agents/lib/api"

const ratings: Array<{
  value: ThreadFeedbackRating
  label: string
  icon: Glyph
}> = [
  { value: "good", label: "Good", icon: ThumbsUp },
  { value: "bad", label: "Bad", icon: ThumbsDown },
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
      <Alert role="status" tone="positive" icon={CheckCircle}>
        <AlertDescription>Thanks for your feedback.</AlertDescription>
      </Alert>
    )
  }

  return (
    <Stack
      render={
        <form
          aria-label="Thread feedback"
          onSubmit={(event) => {
            event.preventDefault()
            if (!showComment || mutation.isPending || !comment.trim()) return
            mutation.mutate({ action: "comment", comment: comment.trim() })
          }}
        />
      }
      gap="md"
      bg="panel"
      border="line"
      radius="panel"
      padding="lg"
    >
      {showComment ? (
        <>
          <Box render={<p />} className="text-label font-medium text-ink">
            How could Open SWE do better?
          </Box>
          <Stack
            render={<label />}
            gap="xs"
            className="text-meta text-ink-subtle"
          >
            Comment (optional)
            <Textarea
              autoFocus
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={3000}
              disabled={mutation.isPending}
              placeholder="What could be better?"
              className="min-h-20 text-body"
            />
          </Stack>
          <Inline gap="sm" align="center">
            <Button
              type="submit"
              size="compact"
              disabled={!comment.trim() || mutation.isPending}
            >
              {mutation.isPending ? "Saving…" : "Submit comment"}
            </Button>
            <Button
              type="button"
              size="compact"
              variant="ghost"
              disabled={mutation.isPending}
              onClick={() => {
                setShowComment(false)
                setShowConfirmation(true)
              }}
            >
              Skip
            </Button>
          </Inline>
        </>
      ) : (
        <Inline gap="md" align="center" justify="between" wrap>
          <Box render={<p />} className="text-label font-medium text-ink">
            How did Open SWE do?
          </Box>
          <Inline gap="sm" align="center" wrap role="group" aria-label="Rating">
            {ratings.map((option) => (
              <Button
                key={option.value}
                type="button"
                variant="outline"
                size="compact"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate({ rating: option.value })}
              >
                <Icon icon={option.icon} size="sm" />
                {option.label}
              </Button>
            ))}
            <Button
              type="button"
              size="compact"
              variant="ghost"
              className="text-ink-subtle hover:text-ink"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate({ action: "dismiss" })}
            >
              Dismiss
            </Button>
          </Inline>
        </Inline>
      )}
    </Stack>
  )
}
