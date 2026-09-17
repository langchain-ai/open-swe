import { useState } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { ConfirmCloseDialog } from "./ConfirmCloseDialog"
import { PullRequestActionButton } from "./PullRequestActionButton"

export function ClosePullRequest({
  pr,
  onClosed,
}: {
  pr: OpenPullRequest
  onClosed: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const close = usePullRequestAction({ pr, action: "close", onDone: onClosed })
  return (
    <PullRequestActionButton
      label={actionLabel(githubActions.close.labels, close)}
      disabled={close.isPending || close.isSuccess}
      onClick={() => setConfirming(true)}
      errors={[close.error]}
    >
      {confirming && (
        <ConfirmCloseDialog
          pr={pr}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false)
            close.mutate()
          }}
        />
      )}
    </PullRequestActionButton>
  )
}
