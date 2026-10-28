import { CaretDownIcon, CheckIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { ButtonGroup } from "@langchain/macaw-components/ButtonGroup"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Link } from "@langchain/macaw-components/Link"
import { RadioButton } from "@langchain/macaw-components/RadioButton"
import { RadioGroup } from "@langchain/macaw-components/RadioGroup"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { SubmitShortcut } from "@/components/SubmitShortcut"
import type { OpenPullRequest, PullRequestReviewEvent } from "@/lib/api"
import { api, ApiError } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"
import {
  markPullRequestReady,
  pullRequestStatusQuery,
} from "@/features/reviews/lib/cache"
import {
  actionLabel,
  githubActions,
} from "@/features/reviews/lib/githubActions"
import {
  standingReview,
  withSubmittedReview,
} from "@/features/reviews/lib/reviewers"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { useIsPullRequestAuthor } from "@/features/reviews/lib/useIsPullRequestAuthor"
import { usePullRequestAction } from "@/features/reviews/lib/usePullRequestAction"
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

// The kit has no success button, so Approve paints its own; dark mode's green is pale.
const approveColors =
  "bg-success-strong! border-transparent! text-white! dark:text-black! hover:brightness-110"

/**
 * The header's review action: one click for the viewer's next step (Approve,
 * or Mark ready on their own draft), with GitHub's review form behind it.
 */
export function ReviewButton({
  owner,
  repo,
  number,
  open,
  onOpenChange: setOpen,
  defaultVerdict,
  onOpenReview,
}: {
  owner: string
  repo: string
  number: number
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The verdict the form opens on, e.g. from an "approve" shortcut. */
  defaultVerdict: PullRequestReviewEvent
  /** Opens the form on a verdict, e.g. to show why a one-click approval failed. */
  onOpenReview: (verdict: PullRequestReviewEvent) => void
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
  if (open && openedOn === null) {
    setOpenedOn(defaultVerdict)
    setEvent(defaultVerdict)
  } else if (!open && openedOn !== null) {
    setOpenedOn(null)
  }
  const [body, setBody] = useState("")
  const needsBody = event !== "APPROVE" && pendingCount === 0
  const submit = useMutation({
    retry: false,
    mutationFn: (review: { event: PullRequestReviewEvent; body: string }) =>
      api.submitPullRequestReview(owner, repo, number, review),
    meta: { errorTitle: "Couldn't submit the review", silent: true },
    onMutate: async (review) => {
      if (!login) return {}
      const undo = await optimisticUpdate<OpenPullRequest | null>(
        queryClient,
        pullRequestStatusQuery(login, { repo: `${owner}/${repo}`, number })
          .queryKey,
        (current) =>
          withSubmittedReview(
            current,
            { login, avatarUrl: session.data?.avatar_url ?? null },
            review.event
          )
      )
      return { undo }
    },
    onError: (_error, _review, context) => context?.undo?.(),
    onSuccess: (result, review) => {
      toast.success(
        review.event === "APPROVE" ? "Approved" : "Review submitted",
        {
          action: {
            label: "View on GitHub",
            onClick: () =>
              window.open(result.html_url, "_blank", "noopener,noreferrer"),
          },
        }
      )
      refresh.reviewed()
      void pending.invalidate()
    },
  })
  const submitForm = () =>
    submit.mutate(
      { event, body: body.trim() },
      {
        onSuccess: () => {
          setOpen(false)
          setBody("")
          setEvent("COMMENT")
        },
      }
    )
  // The form explains a failed one-click approval and keeps its recovery links.
  const approve = () =>
    submit.mutate(
      { event: "APPROVE", body: "" },
      { onError: () => onOpenReview("APPROVE") }
    )
  const canSubmit = !submit.isPending && (!needsBody || body.trim().length > 0)
  const submitLabel = submit.isPending
    ? "Submitting…"
    : isAuthor
      ? "Comment"
      : "Submit review"
  const pendingLabel =
    pendingCount > 0 ? `, ${plural(pendingCount, "pending comment")}` : ""
  const pendingTag = pendingCount > 0 ? String(pendingCount) : undefined

  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (!submit.isPending) setOpen(value)
      }}
    >
      {isAuthor ? (
        <div className="flex items-center gap-space-2">
          {status.data?.draft && (
            <MarkReadyButton pr={status.data} onReady={refresh.status} />
          )}
          <PopoverTrigger asChild>
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              tagText={pendingTag}
              aria-label={`Comment${pendingLabel}`}
            >
              Comment
            </Button>
          </PopoverTrigger>
        </div>
      ) : (
        <ButtonGroup size="sm" color="primary" variant="normal">
          <Button
            leftDecorator={approved ? CheckIcon : undefined}
            tagText={approved ? undefined : pendingTag}
            disabled={approved || submit.isPending}
            aria-label={approved ? "Approved" : `Approve${pendingLabel}`}
            aria-live="polite"
            onClick={approve}
            className={cn(
              approved
                ? "border-success! bg-success! text-success-secondary!"
                : approveColors
            )}
          >
            {approved ? "Approved" : "Approve"}
          </Button>
          <PopoverTrigger asChild>
            <IconButton
              icon={CaretDownIcon}
              label="Comment or request changes"
              tooltipProps={{ disabled: true }}
              className={cn(
                approved
                  ? "border-success! bg-success! text-success-secondary! hover:brightness-110"
                  : [
                      approveColors,
                      "border-l! border-l-white/30! dark:border-l-black/20!",
                    ]
              )}
            />
          </PopoverTrigger>
        </ButtonGroup>
      )}
      <PopoverContent
        align="end"
        aria-label="Finish your review"
        className="w-96 max-w-[calc(100vw-2rem)]"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) submitForm()
          }}
          onKeyDown={(e) => {
            if (
              e.target instanceof Element &&
              e.target.closest('a, button:not([role="radio"])')
            )
              return
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault()
              e.currentTarget.requestSubmit()
            }
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
            <div role="alert" className="mt-space-2 space-y-space-1">
              <Text
                as="p"
                variant="xs"
                className="break-words text-error-secondary"
              >
                {submit.error instanceof ApiError && submit.error.status < 500
                  ? `Review submission was rejected: ${submit.error.message}`
                  : `We couldn't confirm your review was submitted: ${submit.error.message}`}
              </Text>
              <Text as="p" variant="xs" className="text-secondary">
                {submit.error instanceof ApiError && submit.error.status < 500
                  ? `Your draft is preserved.${submit.error.status === 404 ? " Check that you can access this pull request on GitHub." : " Address the error before submitting again."}`
                  : "Your draft is preserved. Check GitHub before submitting again; the review may already have been posted."}
              </Text>
              <Link
                variant="xs"
                href={`https://github.com/${owner}/${repo}/pull/${number}#pullrequestreview`}
                target="_blank"
                rel="noopener noreferrer"
              >
                Check review on GitHub
              </Link>
            </div>
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
            <Button
              type="submit"
              size="xs"
              aria-label={submitLabel}
              aria-keyshortcuts="Meta+Enter Control+Enter"
              disabled={!canSubmit}
            >
              {submitLabel}
              {!submit.isPending && <SubmitShortcut />}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}

/** The author's one click out of draft. */
function MarkReadyButton({
  pr,
  onReady,
}: {
  pr: OpenPullRequest
  onReady: () => void
}) {
  const queryClient = useQueryClient()
  const ready = usePullRequestAction({
    pr,
    action: "mark-ready",
    apply: () => markPullRequestReady(queryClient, pr),
    onDone: onReady,
  })
  return (
    <Button
      size="sm"
      disabled={ready.isPending || ready.isSuccess}
      aria-live="polite"
      onClick={() => ready.mutate()}
    >
      {actionLabel(githubActions["mark-ready"].labels, ready)}
    </Button>
  )
}
