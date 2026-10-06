import { useQuery } from "@tanstack/react-query"
import { useEffect } from "react"

import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"
import { getReviewConversation } from "@/features/reviews/lib/conversationApi"
import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

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
    <Stack
      gap="md"
      bg="panel"
      border="line"
      radius="panel"
      padding="md"
      className="w-full shrink-0 text-label"
      data-testid="proposed-comment"
    >
      <Stack gap="xs" className="min-w-0">
        <Box render={<h3 />} className="font-medium text-ink">
          {submitted || outcome?.state === "posted"
            ? "Submitted with your review"
            : outcome?.state === "added"
              ? "Added to your review"
              : outcome?.state === "discarded"
                ? "Comment discarded"
                : "Draft review comment"}
        </Box>
        <Box className="min-w-0 text-meta text-ink-subtle">
          {onShow ? (
            <button
              type="button"
              onClick={onShow}
              className="max-w-full truncate font-mono underline-offset-2 hover:text-ink hover:underline"
            >
              {location}
            </button>
          ) : (
            <span className="font-mono">{location}</span>
          )}
        </Box>
      </Stack>
      {outcome ? (
        <p className="line-clamp-3 whitespace-pre-wrap text-ink-subtle">
          {body}
        </p>
      ) : (
        <Textarea
          aria-label="Comment body"
          value={body}
          onChange={(event) => drafts.edit(id, { body: event.target.value })}
          rows={4}
          disabled={post.isPending}
        />
      )}
      {outcome ? null : (
        <Inline gap="sm" justify="end">
          <Button
            size="compact"
            variant="ghost"
            disabled={post.isPending}
            onClick={() => drafts.settle(id, { state: "discarded" })}
          >
            Discard
          </Button>
          <Button
            size="compact"
            disabled={!body.trim()}
            loading={post.isPending}
            onClick={addToReview}
          >
            Add to review
          </Button>
        </Inline>
      )}
    </Stack>
  )
}
