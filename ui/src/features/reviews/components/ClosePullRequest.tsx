import { Button } from "@langchain/macaw-components/Button"
import { useIsMutating } from "@tanstack/react-query"
import type { OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import { pullRequestKey } from "../lib/status"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { TextPopover } from "./TextPopover"

export function ClosePullRequest({
  pr,
  apply,
  onClosed,
}: {
  pr: OpenPullRequest
  apply?: () => () => void
  onClosed?: () => void
}) {
  const mutationKey = ["pull-request-close", pr.repo.toLowerCase(), pr.number]
  const isPending = useIsMutating({ mutationKey }) > 0
  const close = usePullRequestAction({
    pr,
    action: "close",
    mutationKey,
    apply,
    onDone: onClosed,
  })
  return (
    <div className="flex">
      <TextPopover
        trigger={
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            disabled={isPending || close.isSuccess}
            aria-live="polite"
          >
            {actionLabel(githubActions.close.labels, { ...close, isPending })}
          </Button>
        }
        title={`Close ${pullRequestKey(pr)}?`}
        description="GitHub closes it without merging. A reason is posted as a comment."
        placeholder="Reason (optional)"
        submitLabel="Close pull request"
        onSubmit={(reason) =>
          close.mutateAsync({ reason: reason || undefined })
        }
      />
    </div>
  )
}
