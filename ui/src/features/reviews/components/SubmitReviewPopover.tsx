import { Button } from "@langchain/macaw-components/Button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import {
  RadioGroup,
  RadioGroupItem,
} from "@langchain/macaw-components/RadioGroup"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import type { PullRequestReviewEvent } from "@/lib/api"
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
      <PopoverTrigger asChild>
        <Button
          size="xs"
          rightDecorator={CaretDownIcon}
          tagText={pendingCount > 0 ? String(pendingCount) : undefined}
          aria-label={
            pendingCount > 0
              ? `Review changes, ${pendingCount} pending comments`
              : "Review changes"
          }
        >
          Review changes
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Finish your review"
        className="w-96 max-w-[calc(100vw-2rem)]"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) submit.mutate()
          }}
        >
          <Text as="h2" variant="sm" weight="semibold">
            Finish your review
          </Text>
          {pendingCount > 0 && (
            <p className="mt-1 text-xs text-secondary">
              {pendingCount} pending comment{pendingCount === 1 ? "" : "s"} will
              be submitted with this review.
            </p>
          )}
          <Textarea
            size="md"
            aria-label="Review summary"
            value={body}
            onChange={setBody}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault()
                if (canSubmit) submit.mutate()
              }
            }}
            placeholder="Leave a comment"
            rows={5}
            className="mt-space-2"
            disabled={submit.isPending}
            autoFocus
          />
          <RadioGroup
            aria-label="Review verdict"
            className="mt-space-3 gap-space-2"
            value={event}
            disabled={submit.isPending}
            onValueChange={(value) => {
              const verdict = VERDICTS.find((item) => item.event === value)
              if (verdict) setEvent(verdict.event)
            }}
          >
            {VERDICTS.map((verdict) => (
              <label
                key={verdict.event}
                className="flex cursor-pointer items-start gap-space-2 text-xs"
              >
                <RadioGroupItem value={verdict.event} className="mt-0.5" />
                <span>
                  <span className="font-medium text-primary">
                    {verdict.label}
                  </span>
                  <span className="block text-secondary">
                    {verdict.description}
                  </span>
                </span>
              </label>
            ))}
          </RadioGroup>
          {submit.error && (
            <p className="mt-2 text-xs break-words text-error-secondary">
              {submit.error.message}
            </p>
          )}
          <div className="mt-3 flex items-center justify-end gap-2">
            {pending.review && (
              <Button
                size="xs"
                color="error"
                variant="plain"
                className="mr-auto"
                disabled={submit.isPending || pending.discard.isPending}
                onClick={() => pending.discard.mutate()}
              >
                Discard review
              </Button>
            )}
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={submit.isPending}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="xs" disabled={!canSubmit}>
              {submit.isPending ? "Submitting…" : "Submit review"}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
