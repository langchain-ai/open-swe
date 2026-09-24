import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import type { ReviewEvent } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const EVENTS: ReadonlyArray<[ReviewEvent, string]> = [
  ["COMMENT", "Comment"],
  ["APPROVE", "Approve"],
  ["REQUEST_CHANGES", "Request changes"],
]

const EVENT_LABEL: Record<ReviewEvent, string> = {
  COMMENT: "Comment",
  APPROVE: "Approve",
  REQUEST_CHANGES: "Request changes",
}

/** A whole-PR review the chat drafted; nothing submits until the user confirms. */
export function ProposedReviewCard({
  owner,
  repo,
  number,
  id,
}: {
  owner: string
  repo: string
  number: number
  id: string
}) {
  const queryClient = useQueryClient()
  const drafts = useChatDrafts()
  const draft = drafts?.reviews.find((item) => item.proposal.id === id)
  const pending = usePendingReview(owner, repo, number)
  const pendingCount = pending.comments.length
  const submit = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("The draft is no longer available")
      return api.submitPullRequestReview(owner, repo, number, {
        event: draft.event,
        body: draft.body.trim(),
      })
    },
    onSuccess: (result) => {
      drafts?.settle(id, { state: "posted", url: result.html_url })
      void queryClient.invalidateQueries({
        queryKey: ["review", owner, repo, number],
      })
      void pending.invalidate()
    },
    onError: (error) =>
      toast.error("Couldn't submit the review", {
        description: error.message,
      }),
  })
  if (!drafts || !draft) return null

  const { outcome, body, event } = draft
  const needsBody = event !== "APPROVE" && pendingCount === 0
  return (
    <Card size="sm" className="w-full shrink-0" data-testid="proposed-review">
      <CardHeader>
        <CardTitle>
          {outcome?.state === "posted"
            ? `Review submitted: ${EVENT_LABEL[event]}`
            : outcome?.state === "discarded"
              ? "Review discarded"
              : "Draft review"}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {!outcome && pendingCount > 0 && (
          <p className="text-muted-foreground">
            Includes your {pendingCount} pending comment
            {pendingCount === 1 ? "" : "s"}.
          </p>
        )}
        {!outcome && (
          <div
            role="radiogroup"
            aria-label="Review verdict"
            className="flex gap-1"
          >
            {EVENTS.map(([value, label]) => (
              <Button
                key={value}
                size="sm"
                role="radio"
                aria-checked={event === value}
                variant={event === value ? "secondary" : "ghost"}
                className={cn(
                  event === value &&
                    value === "REQUEST_CHANGES" &&
                    "text-destructive"
                )}
                disabled={submit.isPending}
                onClick={() => drafts.edit(id, { event: value })}
              >
                {label}
              </Button>
            ))}
          </div>
        )}
        {outcome ? (
          body && (
            <p className="line-clamp-3 whitespace-pre-wrap text-muted-foreground">
              {body}
            </p>
          )
        ) : (
          <Textarea
            aria-label="Review body"
            value={body}
            placeholder={needsBody ? "Required" : "Optional"}
            onChange={(e) => drafts.edit(id, { body: e.target.value })}
            rows={4}
            disabled={submit.isPending}
          />
        )}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {outcome?.state === "posted" ? (
          <Button
            size="sm"
            variant="outline"
            render={
              <a href={outcome.url} target="_blank" rel="noopener noreferrer" />
            }
          >
            View on GitHub
          </Button>
        ) : outcome ? null : (
          <>
            <Button
              size="sm"
              variant="ghost"
              disabled={submit.isPending}
              onClick={() => drafts.settle(id, { state: "discarded" })}
            >
              Discard
            </Button>
            <Button
              size="sm"
              variant={event === "REQUEST_CHANGES" ? "destructive" : "default"}
              disabled={submit.isPending || (needsBody && !body.trim())}
              onClick={() => submit.mutate()}
            >
              {submit.isPending ? "Submitting…" : "Submit as you"}
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  )
}
