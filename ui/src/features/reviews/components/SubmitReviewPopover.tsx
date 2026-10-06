import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useId, useState } from "react"
import { toast } from "sonner"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import {
  RadioGroup,
  RadioGroupItem,
} from "@langchain/gtm-platform-design-system/ui/radio-group"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

import { AlertTriangle, ChevronDown } from "@/components/glyphs"
import type { PullRequestReviewEvent } from "@/lib/api"
import { api } from "@/lib/api"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"

function isVerdict(value: unknown): value is PullRequestReviewEvent {
  return VERDICTS.some((verdict) => verdict.event === value)
}

const VERDICTS: ReadonlyArray<{
  event: PullRequestReviewEvent
  label: string
  description: string
}> = [
  {
    event: "COMMENT",
    label: "Comment",
    description: "Submit general feedback without explicit approval.",
  },
  {
    event: "APPROVE",
    label: "Approve",
    description: "Submit feedback and approve merging these changes.",
  },
  {
    event: "REQUEST_CHANGES",
    label: "Request changes",
    description: "Submit feedback that must be addressed before merging.",
  },
]

/** GitHub's "Review changes" form: a summary plus a verdict, submitted as the signed-in user. */
export function SubmitReviewPopover({
  owner,
  repo,
  number,
}: {
  owner: string
  repo: string
  number: number
}) {
  const queryClient = useQueryClient()
  const pending = usePendingReview(owner, repo, number)
  const pendingCount = pending.comments.length
  const [open, setOpen] = useState(false)
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)
  const [event, setEvent] = useState<PullRequestReviewEvent>("COMMENT")
  const [body, setBody] = useState("")
  const titleId = useId()
  const needsBody = event !== "APPROVE" && pendingCount === 0
  const submit = useMutation({
    mutationFn: () =>
      api.submitPullRequestReview(owner, repo, number, {
        event,
        body: body.trim(),
      }),
    meta: { errorTitle: "Couldn't submit the review", silent: true },
    onSuccess: (result) => {
      setOpen(false)
      setBody("")
      setEvent("COMMENT")
      toast.success("Review submitted", {
        action: {
          label: "View on GitHub",
          onClick: () =>
            window.open(result.html_url, "_blank", "noopener,noreferrer"),
        },
      })
      void queryClient.invalidateQueries({
        queryKey: ["review", owner, repo, number],
      })
      void pending.invalidate()
      void queryClient.invalidateQueries({
        queryKey: reviewConversationQueryKey(owner, repo, number),
      })
    },
  })
  const canSubmit = !submit.isPending && (!needsBody || body.trim().length > 0)

  return (
    <>
      <Popover
        open={open}
        onOpenChange={(value) => {
          if (!submit.isPending) setOpen(value)
        }}
      >
        <PopoverTrigger render={<Button size="compact" />}>
          Review changes
          {pendingCount > 0 && (
            <Badge
              tier="chip"
              aria-label={`${pendingCount} pending comments`}
              className="text-primary-ink"
            >
              {pendingCount}
            </Badge>
          )}
          <Icon icon={ChevronDown} size="sm" />
        </PopoverTrigger>
        <PopoverContent
          align="end"
          aria-labelledby={titleId}
          className="w-96 max-w-(--available-width)"
        >
          <Stack
            render={
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  if (canSubmit) submit.mutate()
                }}
              />
            }
            gap="md"
          >
            <Stack gap="xs">
              <Box
                render={<h2 id={titleId} />}
                className="text-label font-medium text-ink"
              >
                Finish your review
              </Box>
              {pendingCount > 0 && (
                <p className="text-meta text-ink-subtle">
                  {pendingCount} pending comment{pendingCount === 1 ? "" : "s"}{" "}
                  will be submitted with this review.
                </p>
              )}
            </Stack>
            <Textarea
              aria-label="Review summary"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault()
                  if (canSubmit) submit.mutate()
                }
              }}
              placeholder="Leave a comment"
              rows={5}
              className="resize-y"
              disabled={submit.isPending}
              autoFocus
            />
            <RadioGroup
              aria-label="Review verdict"
              value={event}
              onValueChange={(value: unknown) => {
                if (isVerdict(value)) setEvent(value)
              }}
              disabled={submit.isPending}
              className="gap-3"
            >
              {VERDICTS.map((verdict) => (
                <Inline
                  key={verdict.event}
                  render={<label />}
                  gap="sm"
                  align="start"
                  className="cursor-pointer text-label"
                >
                  <RadioGroupItem value={verdict.event} className="mt-0.5" />
                  <Stack gap="none">
                    <span className="font-medium text-ink">
                      {verdict.label}
                    </span>
                    <span className="text-meta text-ink-subtle">
                      {verdict.description}
                    </span>
                  </Stack>
                </Inline>
              ))}
            </RadioGroup>
            {submit.error && (
              <Inline
                role="alert"
                gap="xs"
                align="start"
                className="text-label break-words text-risk"
              >
                <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
                <span>{submit.error.message}</span>
              </Inline>
            )}
            <Inline gap="sm" justify="end">
              {pending.review && (
                <Button
                  type="button"
                  size="compact"
                  variant="ghost"
                  className="mr-auto text-risk"
                  disabled={submit.isPending || pending.discard.isPending}
                  onClick={() => {
                    setOpen(false)
                    setConfirmingDiscard(true)
                  }}
                >
                  Discard review
                </Button>
              )}
              <Button
                type="button"
                size="compact"
                variant="ghost"
                disabled={submit.isPending}
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="compact"
                disabled={!canSubmit}
                loading={submit.isPending}
              >
                Submit review
              </Button>
            </Inline>
          </Stack>
        </PopoverContent>
      </Popover>
      {confirmingDiscard && (
        <ConfirmableAction
          title="Discard your pending review?"
          description={`Its ${pendingCount} pending comment${pendingCount === 1 ? "" : "s"} are deleted from GitHub and cannot be recovered.`}
          confirmLabel="Discard review"
          onConfirm={() => pending.discard.mutateAsync().then(() => undefined)}
          onDismiss={() => setConfirmingDiscard(false)}
        />
      )}
    </>
  )
}
