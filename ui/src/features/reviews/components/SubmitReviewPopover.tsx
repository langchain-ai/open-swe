import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { RadioButton } from "@langchain/macaw-components/RadioButton"
import { RadioGroup } from "@langchain/macaw-components/RadioGroup"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import type { OpenPullRequest, PullRequestReviewEvent } from "@/lib/api"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
import { pullRequestStatusQuery } from "@/features/reviews/lib/cache"
import {
  standingReview,
  withSubmittedReview,
} from "@/features/reviews/lib/reviewers"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { useIsPullRequestAuthor } from "@/features/reviews/lib/useIsPullRequestAuthor"
import { usePullRequestStatus } from "@/features/reviews/lib/usePullRequestStatus"
import { useRefreshPullRequest } from "@/features/reviews/page/queries"
import { plural } from "@/features/reviews/page/text"

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
  open,
  onOpenChange: setOpen,
  defaultVerdict,
}: {
  owner: string
  repo: string
  number: number
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The verdict the form opens on, e.g. from an "approve" shortcut. */
  defaultVerdict: PullRequestReviewEvent
}) {
  const queryClient = useQueryClient()
  const session = useSession()
  const login = session.data?.login
  const refresh = useRefreshPullRequest({ owner, repo, number })
  const pending = usePendingReview(owner, repo, number)
  const pendingCount = pending.comments.length
  const status = usePullRequestStatus(`${owner}/${repo}`, number)
  const approved = standingReview(status.data, login) === "approved"
  // GitHub refuses an author's approval or change request on their own PR.
  const isAuthor = useIsPullRequestAuthor(owner, repo, number)
  const [chosen, setEvent] = useState<PullRequestReviewEvent>(defaultVerdict)
  const event: PullRequestReviewEvent = isAuthor ? "COMMENT" : chosen
  const [openedOn, setOpenedOn] = useState<PullRequestReviewEvent | null>(null)
  if (open && openedOn !== defaultVerdict) {
    setOpenedOn(defaultVerdict)
    setEvent(defaultVerdict)
  } else if (!open && openedOn !== null) {
    setOpenedOn(null)
  }
  const [body, setBody] = useState("")
  const needsBody = event !== "APPROVE" && pendingCount === 0
  const submit = useMutation({
    mutationFn: () =>
      api.submitPullRequestReview(owner, repo, number, {
        event,
        body: body.trim(),
      }),
    meta: { errorTitle: "Couldn't submit the review", silent: true },
    onMutate: async () => {
      if (!login) return {}
      const undo = await optimisticUpdate<OpenPullRequest | null>(
        queryClient,
        pullRequestStatusQuery(login, { repo: `${owner}/${repo}`, number })
          .queryKey,
        (current) =>
          withSubmittedReview(
            current,
            { login, avatarUrl: session.data?.avatar_url ?? null },
            event
          )
      )
      return { undo }
    },
    onError: (_error, _variables, context) => context?.undo?.(),
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
      refresh.reviewed()
      void pending.invalidate()
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
          size="sm"
          rightDecorator={CaretDownIcon}
          tagText={pendingCount > 0 ? String(pendingCount) : undefined}
          aria-label={
            pendingCount > 0
              ? `Review changes, ${plural(pendingCount, "pending comment")}`
              : "Review changes"
          }
        >
          Review<span className="max-sm:hidden"> changes</span>
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
            {isAuthor ? "Comment on your pull request" : "Finish your review"}
          </Text>
          {pendingCount > 0 && (
            <p className="mt-space-1 text-xs text-secondary">
              {plural(pendingCount, "pending comment")} will be submitted with
              this review.
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
          {isAuthor ? (
            <p className="mt-space-2 text-xs text-secondary">
              GitHub doesn&apos;t let you approve or request changes on your own
              pull request.
            </p>
          ) : (
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
              {VERDICTS.map((verdict) => {
                const alreadyGiven = approved && verdict.event === "APPROVE"
                return (
                  <RadioButton
                    key={verdict.event}
                    value={verdict.event}
                    disabled={alreadyGiven}
                    className="items-start"
                    label={
                      <>
                        <span className="font-medium">{verdict.label}</span>
                        <span className="block text-xs text-secondary">
                          {alreadyGiven
                            ? "You approved these changes."
                            : verdict.description}
                        </span>
                      </>
                    }
                  />
                )
              })}
            </RadioGroup>
          )}
          {submit.error && (
            <p className="mt-space-2 text-xs break-words text-error-secondary">
              {submit.error.message}
            </p>
          )}
          <div className="mt-space-3 flex items-center justify-end gap-space-2">
            {pending.review && (
              <Button
                type="button"
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
              type="button"
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={submit.isPending}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="xs" disabled={!canSubmit}>
              {submit.isPending
                ? "Submitting…"
                : isAuthor
                  ? "Comment"
                  : "Submit review"}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
