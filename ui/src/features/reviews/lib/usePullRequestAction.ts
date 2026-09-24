import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import type {
  MergeMethod,
  OpenPullRequest,
  PullRequestActionName,
} from "@/lib/api"
import { githubActions } from "./githubActions"
import { pullRequestKey } from "./status"

/** Runs one GitHub action on one pull request and reports it in a toast. */
export function usePullRequestAction({
  pr,
  action,
  onDone,
  method,
}: {
  pr: OpenPullRequest
  action: PullRequestActionName
  onDone: () => void
  method?: MergeMethod
}) {
  const { run, succeeded, failed } = githubActions[action]
  return useMutation({
    mutationFn: (reason: string | void) => run(pr, method, reason || undefined),
    meta: { errorTitle: failed(pullRequestKey(pr)) },
    onSuccess: () => {
      toast.success(succeeded(pullRequestKey(pr)))
      onDone()
    },
    retry: false,
  })
}
