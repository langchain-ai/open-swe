import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import {
  threadActions,
  type PullRequestThreadActionName,
} from "../lib/threadActions"
import { PullRequestActionButton } from "./PullRequestActionButton"

type PullRequestThreadStatus = Awaited<
  ReturnType<typeof api.pullRequestThreadStatus>
>

/**
 * A button that dispatches agent work onto the pull request's own thread.
 * One thread per pull request, so any of these actions blocks the others
 * while a run is live.
 */
export function PullRequestThreadAction({
  pr,
  login,
  action,
}: {
  pr: OpenPullRequest
  login: string
  action: PullRequestThreadActionName
}) {
  const { labels, toasts, run: dispatch } = threadActions[action]
  const queryClient = useQueryClient()
  const thread = useQuery({
    queryKey: ["pr-thread-status", login, pr.repo, pr.number],
    queryFn: () => api.pullRequestThreadStatus(pr.repo, pr.number),
    ...expiresInBrowser,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const run = useMutation({
    mutationFn: (target: OpenPullRequest) => dispatch(target),
    meta: { errorTitle: `${toasts.failed} ${pr.repo}#${pr.number}` },
    onMutate: async (target) => {
      const key = ["pr-thread-status", login, target.repo, target.number]
      await queryClient.cancelQueries({ queryKey: key, exact: true })
      const previous = queryClient.getQueryData<PullRequestThreadStatus>(key)
      queryClient.setQueryData<PullRequestThreadStatus>(key, {
        ...previous,
        running: true,
      })
      return { key, previous }
    },
    onError: (_error, _target, context) => {
      if (context) queryClient.setQueryData(context.key, context.previous)
    },
    onSuccess: (result, target) => {
      toast.success(
        `${result.already_running ? toasts.running : toasts.queued} ${target.repo}#${target.number}`
      )
    },
  })
  return (
    <PullRequestActionButton
      label={
        run.data?.already_running
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
      }
      disabled={
        thread.isPending ||
        thread.isError ||
        thread.data?.running === true ||
        run.isPending ||
        run.isSuccess
      }
      onClick={() => run.mutate(pr)}
      errors={[thread.error]}
    />
  )
}
