import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import type {
  MergeMethod,
  OpenPullRequest,
  PullRequestActionName,
} from "@/lib/api"
import { githubActions } from "./githubActions"
import { pullRequestKey } from "./status"

/**
 * Runs one GitHub action on one pull request and reports it in a toast.
 * `apply` shows the result before GitHub answers and returns its undo.
 */
export function usePullRequestAction({
  pr,
  action,
  apply,
  onDone,
  method,
}: {
  pr: OpenPullRequest
  action: PullRequestActionName
  apply?: () => () => void
  onDone?: () => void
  method?: MergeMethod
}) {
  const { run, succeeded, failed } = githubActions[action]
  return useMutation({
    mutationFn: (reason: string | void) => run(pr, method, reason || undefined),
    meta: { errorTitle: failed(pullRequestKey(pr)) },
    onMutate: () => ({ undo: apply?.() }),
    onError: (_error, _reason, context) => context?.undo?.(),
    onSuccess: (result, _reason, context) => {
      if (result?.auto_merge) {
        context?.undo?.()
        toast.success(`Auto-merge enabled for ${pullRequestKey(pr)}`)
        return
      }
      toast.success(succeeded(pullRequestKey(pr)))
      onDone?.()
    },
    retry: false,
  })
}
