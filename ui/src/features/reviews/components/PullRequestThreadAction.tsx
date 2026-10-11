import { DropdownMenuItem } from "@langchain/macaw-components/DropdownMenu"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { SplitButton } from "@/components/SplitButton"
import {
  threadActions,
  type PullRequestThreadActionName,
} from "../lib/threadActions"

type PullRequestThreadStatus = Awaited<
  ReturnType<typeof api.pullRequestThreadStatus>
>

function threadStatusKey(pr: OpenPullRequest, login: string) {
  return ["pr-thread-status", login, pr.repo, pr.number] as const
}

/** Starting one action on the PR's thread, marking the thread busy until GitHub answers. */
function useRun(
  pr: OpenPullRequest,
  login: string,
  action: PullRequestThreadActionName
) {
  const queryClient = useQueryClient()
  const key = threadStatusKey(pr, login)
  const { toasts } = threadActions[action]
  return useMutation({
    mutationFn: () => threadActions[action].run(pr),
    meta: { errorTitle: `${toasts.failed} ${pr.repo}#${pr.number}` },
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: key, exact: true })
      const previous = queryClient.getQueryData<PullRequestThreadStatus>(key)
      queryClient.setQueryData<PullRequestThreadStatus>(key, {
        ...previous,
        running: true,
      })
      return { previous }
    },
    onError: (_error, _variables, context) =>
      queryClient.setQueryData(key, context?.previous),
    onSuccess: (result) =>
      toast.success(
        `${result.already_running ? toasts.running : toasts.queued} ${pr.repo}#${pr.number}`
      ),
  })
}

/**
 * Dispatches agent work onto the pull request's own thread. One thread per
 * pull request, so any of these actions blocks the others while a run is live.
 * The first action is the button; the caret offers the rest.
 */
export function PullRequestThreadAction({
  pr,
  login,
  actions,
}: {
  pr: OpenPullRequest
  login: string
  actions: ReadonlyArray<PullRequestThreadActionName>
}) {
  const thread = useQuery({
    queryKey: threadStatusKey(pr, login),
    queryFn: () => api.pullRequestThreadStatus(pr.repo, pr.number),
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  // A fixed set of hooks, whichever actions this PR needs.
  const runs: Record<PullRequestThreadActionName, ReturnType<typeof useRun>> = {
    "fix-conflicts": useRun(pr, login, "fix-conflicts"),
    "fix-checks": useRun(pr, login, "fix-checks"),
    "address-comments": useRun(pr, login, "address-comments"),
  }
  const [first, ...rest] = actions
  if (!first) return null
  const started = actions.find((action) => !runs[action].isIdle) ?? first
  const run = runs[started]
  const { labels } = threadActions[started]
  const label = run.data?.already_running
    ? labels.running
    : run.isPending || run.isSuccess
      ? labels.queued
      : thread.data?.running
        ? labels.running
        : thread.isPending
          ? labels.checking
          : thread.isError
            ? labels.unavailable
            : run.isError
              ? labels.retry
              : labels.idle
  return (
    <div>
      <SplitButton
        disabled={
          thread.isPending ||
          thread.isError ||
          thread.data?.running === true ||
          run.isPending ||
          run.isSuccess
        }
        onClick={() => run.mutate()}
        menuLabel={`More fixes for PR #${pr.number}`}
        menuAlign="start"
        menu={
          rest.length > 0 &&
          rest.map((action) => (
            <DropdownMenuItem
              key={action}
              onSelect={() => runs[action].mutate()}
            >
              {threadActions[action].labels.idle}
            </DropdownMenuItem>
          ))
        }
      >
        {label}
      </SplitButton>
      {thread.error && (
        <p role="alert" className="mt-space-1 text-error-secondary">
          {thread.error.message}
        </p>
      )}
    </div>
  )
}
