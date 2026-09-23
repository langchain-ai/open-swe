import type { OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { PullRequestActionButton } from "./PullRequestActionButton"

export function UpdatePullRequestBranch({
  pr,
  onUpdated,
}: {
  pr: OpenPullRequest
  onUpdated: () => void
}) {
  const update = usePullRequestAction({
    pr,
    action: "update-branch",
    onDone: onUpdated,
  })
  return (
    <PullRequestActionButton
      label={actionLabel(githubActions["update-branch"].labels, update)}
      disabled={!pr.headSha || update.isPending || update.isSuccess}
      onClick={() => update.mutate()}
      errors={[update.error]}
    />
  )
}
