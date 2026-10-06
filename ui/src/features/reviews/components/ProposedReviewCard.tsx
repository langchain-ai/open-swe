import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { ReviewEvent } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { reviewConversationQueryKey } from "@/features/reviews/components/ReviewConversation"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import {
  RadioGroup,
  RadioGroupItem,
} from "@langchain/gtm-platform-design-system/ui/radio-group"
import { api } from "@/lib/api"

const EVENTS: ReadonlyArray<[ReviewEvent, string]> = [
  ["COMMENT", "Comment"],
  ["APPROVE", "Approve"],
  ["REQUEST_CHANGES", "Request changes"],
]

function isReviewEvent(value: unknown): value is ReviewEvent {
  return EVENTS.some(([event]) => event === value)
}

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
    <Stack
      gap="md"
      bg="panel"
      border="line"
      radius="panel"
      padding="md"
      className="w-full shrink-0 text-label"
      data-testid="proposed-review"
    >
      <Box render={<h3 />} className="font-medium text-ink">
        {outcome?.state === "posted"
          ? `Review submitted: ${EVENT_LABEL[event]}`
          : outcome?.state === "discarded"
            ? "Review discarded"
            : "Draft review"}
      </Box>
      {!outcome && pendingCount > 0 && (
        <p className="text-meta text-ink-subtle">
          Includes your {pendingCount} pending comment
          {pendingCount === 1 ? "" : "s"}.
        </p>
      )}
      {!outcome && (
        <RadioGroup
          aria-label="Review verdict"
          value={event}
          onValueChange={(value: unknown) => {
            if (isReviewEvent(value)) drafts.edit(id, { event: value })
          }}
          disabled={submit.isPending}
          className="flex flex-wrap gap-4"
        >
          {EVENTS.map(([value, label]) => (
            <Inline
              key={value}
              render={<label />}
              gap="sm"
              className={
                value === "REQUEST_CHANGES" && event === value
                  ? "cursor-pointer text-risk"
                  : "cursor-pointer text-ink"
              }
            >
              <RadioGroupItem value={value} />
              {label}
            </Inline>
          ))}
        </RadioGroup>
      )}
      {outcome ? (
        body && (
          <p className="line-clamp-3 whitespace-pre-wrap text-ink-subtle">
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
      {outcome?.state === "posted" ? (
        <Inline justify="end">
          <a
            href={outcome.url}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: "outline", size: "compact" })}
          >
            View on GitHub
          </a>
        </Inline>
      ) : outcome ? null : (
        <Inline gap="sm" justify="end">
          <Button
            size="compact"
            variant="ghost"
            disabled={submit.isPending}
            onClick={() => drafts.settle(id, { state: "discarded" })}
          >
            Discard
          </Button>
          <Button
            size="compact"
            disabled={needsBody && !body.trim()}
            loading={submit.isPending}
            onClick={() => submit.mutate()}
          >
            Submit as you
          </Button>
        </Inline>
      )}
    </Stack>
  )
}
