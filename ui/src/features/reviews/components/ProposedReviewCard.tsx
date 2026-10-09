import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { ReviewEvent } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"
import { api } from "@/lib/api"

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
      void queryClient.invalidateQueries({
        queryKey: reviewConversationQueryKey(owner, repo, number),
      })
    },
    meta: { errorTitle: "Couldn't submit the review" },
  })
  if (!drafts || !draft) return null

  const { outcome, body, event } = draft
  const needsBody = event !== "APPROVE" && pendingCount === 0
  return (
    <Card
      className="flex w-full shrink-0 flex-col gap-space-3 p-space-3 text-xs"
      data-testid="proposed-review"
    >
      <Text variant="sm" weight="medium">
        {outcome?.state === "posted"
          ? `Review submitted: ${EVENT_LABEL[event]}`
          : outcome?.state === "discarded"
            ? "Review discarded"
            : "Draft review"}
      </Text>
      <div className="flex flex-col gap-space-2">
        {!outcome && pendingCount > 0 && (
          <p className="text-secondary">
            Includes your {pendingCount} pending comment
            {pendingCount === 1 ? "" : "s"}.
          </p>
        )}
        {!outcome && (
          <div
            role="radiogroup"
            aria-label="Review verdict"
            className="flex gap-space-1"
          >
            {EVENTS.map(([value, label]) => (
              <Button
                key={value}
                size="xs"
                role="radio"
                aria-checked={event === value}
                color={
                  event === value && value === "REQUEST_CHANGES"
                    ? "error"
                    : "secondary"
                }
                variant={event === value ? "outlined" : "plain"}
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
            <p className="line-clamp-3 whitespace-pre-wrap text-secondary">
              {body}
            </p>
          )
        ) : (
          <Textarea
            size="md"
            aria-label="Review body"
            value={body}
            placeholder={needsBody ? "Required" : "Optional"}
            onChange={(next) => drafts.edit(id, { body: next })}
            rows={4}
            disabled={submit.isPending}
          />
        )}
      </div>
      {outcome?.state === "posted" ? (
        <div className="flex justify-end">
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            as={
              <a href={outcome.url} target="_blank" rel="noopener noreferrer" />
            }
          >
            View on GitHub
          </Button>
        </div>
      ) : outcome ? null : (
        <div className="flex justify-end gap-space-2">
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            disabled={submit.isPending}
            onClick={() => drafts.settle(id, { state: "discarded" })}
          >
            Discard
          </Button>
          <Button
            size="xs"
            color={event === "REQUEST_CHANGES" ? "error" : "primary"}
            disabled={submit.isPending || (needsBody && !body.trim())}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? "Submitting…" : "Submit as you"}
          </Button>
        </div>
      )}
    </Card>
  )
}
