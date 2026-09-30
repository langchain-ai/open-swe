import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CaretDownIcon } from "@phosphor-icons/react"
import { useState } from "react"
import { toast } from "sonner"

import type { PullRequestReviewEvent } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"

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
  const [event, setEvent] = useState<PullRequestReviewEvent>("COMMENT")
  const [body, setBody] = useState("")
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
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (!submit.isPending) setOpen(value)
      }}
    >
      <PopoverTrigger
        render={
          <Button size="sm">
            Review changes
            {pendingCount > 0 && (
              <span
                aria-label={`${pendingCount} pending comments`}
                className="rounded-full bg-primary-foreground/20 px-1.5 text-[10px] tabular-nums"
              >
                {pendingCount}
              </span>
            )}
            <CaretDownIcon />
          </Button>
        }
      />
      <PopoverPopup align="end" className="w-96 max-w-[calc(100vw-2rem)]">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) submit.mutate()
          }}
        >
          <PopoverTitle className="text-xs">Finish your review</PopoverTitle>
          {pendingCount > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {pendingCount} pending comment{pendingCount === 1 ? "" : "s"} will
              be submitted with this review.
            </p>
          )}
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
            className="mt-2 resize-y text-xs"
            disabled={submit.isPending}
            autoFocus
          />
          <fieldset className="mt-3 flex flex-col gap-2">
            <legend className="sr-only">Review verdict</legend>
            {VERDICTS.map((verdict) => (
              <label
                key={verdict.event}
                className="flex cursor-pointer items-start gap-2 text-xs"
              >
                <input
                  type="radio"
                  name="review-verdict"
                  value={verdict.event}
                  checked={event === verdict.event}
                  onChange={() => setEvent(verdict.event)}
                  disabled={submit.isPending}
                  className="mt-0.5 accent-primary"
                />
                <span>
                  <span className="font-medium text-foreground">
                    {verdict.label}
                  </span>
                  <span className="block text-muted-foreground">
                    {verdict.description}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
          {submit.error && (
            <p className="mt-2 text-xs break-words text-destructive">
              {submit.error.message}
            </p>
          )}
          <div className="mt-3 flex items-center justify-end gap-2">
            {pending.review && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="mr-auto text-destructive"
                disabled={submit.isPending || pending.discard.isPending}
                onClick={() => pending.discard.mutate()}
              >
                Discard review
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={submit.isPending}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={!canSubmit}>
              {submit.isPending ? "Submitting…" : "Submit review"}
            </Button>
          </div>
        </form>
      </PopoverPopup>
    </Popover>
  )
}
