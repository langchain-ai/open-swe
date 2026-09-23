import { useQueryClient } from "@tanstack/react-query"

import type { OpenPullRequest } from "@/lib/api"
import { markPullRequestReady } from "../lib/cache"
import { actionLabel, githubActions } from "../lib/githubActions"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { PullRequestActionButton } from "./PullRequestActionButton"

export function MarkPullRequestReady({
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
    onDone: () => {
      markPullRequestReady(queryClient, pr)
      onReady()
    },
  })
  return (
    <PullRequestActionButton
      label={actionLabel(githubActions["mark-ready"].labels, ready)}
      disabled={ready.isPending || ready.isSuccess}
      onClick={() => ready.mutate()}
      errors={[ready.error]}
    />
  )
}
