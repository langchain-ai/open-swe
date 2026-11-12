import { Button } from "@langchain/macaw-components/Button"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { fixUpAction } from "../lib/threadActions"

type PullRequestThreadStatus = Awaited<
  ReturnType<typeof api.pullRequestThreadStatus>
>

function threadStatusKey(pr: OpenPullRequest, login: string) {
  return ["pr-thread-status", login, pr.repo, pr.number] as const
}

/**
 * Asks the agent on the pull request's own thread to look it over and fix what
 * is clearly broken. One thread per pull request, so a live run blocks another.
 */
export function PullRequestThreadAction({
  pr,
  login,
}: {
  pr: OpenPullRequest
  login: string
}) {
  const queryClient = useQueryClient()
  const key = threadStatusKey(pr, login)
  const thread = useQuery({
    queryKey: key,
    queryFn: () => api.pullRequestThreadStatus(pr.repo, pr.number),
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const { labels, toasts } = fixUpAction
  const run = useMutation({
    mutationFn: () => fixUpAction.run(pr),
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
      <Button
        size="sm"
        color="secondary"
        variant="outlined"
        disabled={
          thread.isPending ||
          thread.isError ||
          thread.data?.running === true ||
          run.isPending ||
          run.isSuccess
        }
        onClick={() => run.mutate()}
      >
        {label}
      </Button>
      {thread.error && (
        <p role="alert" className="mt-space-1 text-error-secondary">
          {thread.error.message}
        </p>
      )}
    </div>
  )
}
