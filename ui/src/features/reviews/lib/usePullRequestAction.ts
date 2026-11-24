import { useMutation, type MutationKey } from "@tanstack/react-query"
import { toast } from "sonner"

import type {
  MergeMethod,
  OpenPullRequest,
  PullRequestActionName,
  PullRequestActionResult,
} from "@/lib/api"
import { type GithubActionInput, githubActions } from "./githubActions"
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
  mutationKey,
}: {
  pr: OpenPullRequest
  action: PullRequestActionName
  apply?: () => () => void
  onDone?: (result: PullRequestActionResult) => void
  method?: MergeMethod
  mutationKey?: MutationKey
}) {
  const { run, succeeded, failed } = githubActions[action]
  return useMutation({
    mutationKey,
    mutationFn: (input: Omit<GithubActionInput, "method"> | void) =>
      run(pr, { ...input, method }),
    meta: { errorTitle: failed(pullRequestKey(pr)) },
    onMutate: () => ({ undo: apply?.() }),
    onError: (_error, _input, context) => context?.undo?.(),
    onSuccess: (result, _input, context) => {
      if (result.auto_merge || result.in_merge_queue) context?.undo?.()
      toast.success(succeeded(pullRequestKey(pr), result))
      onDone?.(result)
    },
    retry: false,
  })
}
