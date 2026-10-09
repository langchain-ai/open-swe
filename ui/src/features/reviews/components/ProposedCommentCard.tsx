import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useQuery } from "@tanstack/react-query"
import { useEffect } from "react"

import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"
import { getReviewConversation } from "@/features/reviews/lib/conversationApi"
import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"

/** A line comment the chat drafted; the user adds it to their pending review or discards it. */
export function ProposedCommentCard({
  owner,
  repo,
  number,
  id,
  onShow,
}: {
  owner: string
  repo: string
  number: number
  id: string
  /** Scrolls the diff to the comment's lines; omitted when already shown there. */
  onShow?: () => void
}) {
  const drafts = useChatDrafts()
  const draft = drafts?.comments.find((item) => item.proposal.id === id)
  const pending = usePendingReview(owner, repo, number)
  const post = pending.add
  const addToReview = () => {
    if (!draft) return
    const { range } = draft.proposal
    const multiLine = range.startLine < range.endLine
    post.mutate(
      {
        path: range.file,
        line: range.endLine,
        side: range.side,
        body: draft.body.trim(),
        start_line: multiLine ? range.startLine : null,
        start_side: multiLine ? range.side : null,
      },
      {
        onSuccess: (review) =>
          drafts?.settle(id, { state: "added", reviewId: review.id }),
      }
    )
  }

  const added = draft?.outcome?.state === "added" ? draft.outcome : null
  const stillPending =
    added !== null &&
    pending.review?.id === added.reviewId &&
    // Match by position: the body may have been edited since it was added.
    pending.comments.some(
      (comment) =>
        comment.path === draft?.proposal.range.file &&
        comment.line === draft.proposal.range.endLine
    )
  const conversation = useQuery({
    queryKey: reviewConversationQueryKey(owner, repo, number),
    queryFn: () => getReviewConversation(owner, repo, number),
    enabled: added !== null && pending.loaded && !stillPending,
  })
  const submitted =
    added !== null &&
    (conversation.data?.items.some(
      (item) => item.kind === "review" && item.id === added.reviewId
    ) ??
      false)
  // An added comment whose pending review lost it (deleted, or the review was
  // discarded) goes back to being an editable draft.
  const orphaned =
    added !== null &&
    pending.loaded &&
    !stillPending &&
    conversation.isSuccess &&
    !conversation.isFetching &&
    conversation.dataUpdatedAt >= pending.updatedAt &&
    !submitted
  const reopen = drafts?.reopen
  useEffect(() => {
    if (orphaned) reopen?.(id)
  }, [orphaned, reopen, id])

  if (!drafts || !draft) return null

  const { outcome, body } = draft
  const { range } = draft.proposal
  const location = `${range.file}:${rangeLabel(range)}`
  return (
    <Card
      className="flex w-full shrink-0 flex-col gap-space-3 p-space-3 text-xs"
      data-testid="proposed-comment"
    >
      <div className="flex flex-col gap-space-1">
        <Text variant="sm" weight="medium">
          {submitted || outcome?.state === "posted"
            ? "Submitted with your review"
            : outcome?.state === "added"
              ? "Added to your review"
              : outcome?.state === "discarded"
                ? "Comment discarded"
                : "Draft review comment"}
        </Text>
        <div className="min-w-0 text-xs text-secondary">
          {onShow ? (
            <button
              type="button"
              onClick={onShow}
              className="truncate font-mono underline-offset-2 hover:underline"
            >
              {location}
            </button>
          ) : (
            <span className="font-mono">{location}</span>
          )}
        </div>
      </div>
      {outcome ? (
        <p className="line-clamp-3 whitespace-pre-wrap text-secondary">
          {body}
        </p>
      ) : (
        <Textarea
          size="md"
          aria-label="Comment body"
          value={body}
          onChange={(next) => drafts.edit(id, { body: next })}
          rows={4}
          disabled={post.isPending}
        />
      )}
      {outcome ? null : (
        <div className="flex justify-end gap-space-2">
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            disabled={post.isPending}
            onClick={() => drafts.settle(id, { state: "discarded" })}
          >
            Discard
          </Button>
          <Button
            size="xs"
            disabled={post.isPending || !body.trim()}
            onClick={addToReview}
          >
            {post.isPending ? "Adding…" : "Add to review"}
          </Button>
        </div>
      )}
    </Card>
  )
}
