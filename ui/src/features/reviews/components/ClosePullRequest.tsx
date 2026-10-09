import { Button } from "@langchain/macaw-components/Button"
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
  apply: () => () => void
  onClosed?: () => void
}) {
  const close = usePullRequestAction({
    pr,
    action: "close",
    apply,
    onDone: onClosed,
  })
  return (
    <div>
      <TextPopover
        trigger={
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
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
        onSubmit={(reason) => close.mutateAsync(reason)}
      />
    </div>
  )
}
