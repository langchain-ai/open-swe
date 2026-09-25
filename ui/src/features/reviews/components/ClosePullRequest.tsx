import { Button } from "@/components/ui/button"
import type { OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import { pullRequestKey } from "../lib/status"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { TextPopover } from "./TextPopover"

export function ClosePullRequest({
  pr,
  onClosed,
}: {
  pr: OpenPullRequest
  onClosed: () => void
}) {
  const close = usePullRequestAction({ pr, action: "close", onDone: onClosed })
  return (
    <div>
      <TextPopover
        trigger={
          <Button
            size="sm"
            variant="outline"
            disabled={close.isPending || close.isSuccess}
            aria-live="polite"
          >
            {actionLabel(githubActions.close.labels, close)}
          </Button>
        }
        title={`Close ${pullRequestKey(pr)}?`}
        description="GitHub closes it without merging. A reason is posted as a comment."
        placeholder="Reason (optional)"
        submitLabel="Close pull request"
        pending={close.isPending}
        onSubmit={(reason, done) => close.mutate(reason, { onSuccess: done })}
      />
    </div>
  )
}
