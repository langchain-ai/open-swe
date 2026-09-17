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
    mutationFn: () => run(pr, method),
    onSuccess: () => {
      toast.success(succeeded(pullRequestKey(pr)))
      onDone()
    },
    onError: (error) =>
      toast.error(failed(pullRequestKey(pr)), { description: error.message }),
    retry: false,
  })
}
