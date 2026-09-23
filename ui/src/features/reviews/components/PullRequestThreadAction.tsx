import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
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
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const run = useMutation({
    mutationFn: (target: OpenPullRequest) => dispatch(target),
    onSuccess: (result, target) => {
      queryClient.setQueryData<PullRequestThreadStatus>(
        ["pr-thread-status", login, target.repo, target.number],
        (old) => ({ ...old, running: true })
      )
      toast.success(
        `${result.already_running ? toasts.running : toasts.queued} ${target.repo}#${target.number}`
      )
    },
    onError: (error, target) =>
      toast.error(`${toasts.failed} ${target.repo}#${target.number}`, {
        description: error.message,
      }),
  })
  return (
    <PullRequestActionButton
      label={
        run.data?.already_running
          ? labels.running
          : run.isSuccess
            ? labels.queued
            : thread.data?.running
              ? labels.running
              : thread.isPending
                ? labels.checking
                : thread.isError
                  ? labels.unavailable
                  : run.isPending
                    ? labels.queuing
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
      errors={[thread.error, run.error]}
    />
  )
}
