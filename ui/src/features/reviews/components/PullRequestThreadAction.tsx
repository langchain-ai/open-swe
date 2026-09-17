import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { PullRequestActionButton } from "./PullRequestActionButton"

export interface ThreadActionLabels {
  idle: string
  running: string
  checking: string
  unavailable: string
  queuing: string
  queued: string
  retry: string
}

export interface ThreadActionToasts {
  queued: string
  running: string
  failed: string
}

/**
 * A button that dispatches agent work onto the pull request's own thread.
 * One thread per pull request, so any of these actions blocks the others
 * while a run is live.
 */
export function PullRequestThreadAction({
  pr,
  login,
  labels,
  toasts,
  dispatch,
}: {
  pr: OpenPullRequest
  login: string
  labels: ThreadActionLabels
  toasts: ThreadActionToasts
  dispatch: (pr: OpenPullRequest) => Promise<{ already_running?: boolean }>
}) {
  const thread = useQuery({
    queryKey: ["pr-thread-status", login, pr.repo, pr.number],
    queryFn: () => api.pullRequestThreadStatus(pr.repo, pr.number),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const run = useMutation({
    mutationFn: () => dispatch(pr),
    onSuccess: (result) =>
      toast.success(
        `${result.already_running ? toasts.running : toasts.queued} ${pr.repo}#${pr.number}`
      ),
    onError: (error) =>
      toast.error(`${toasts.failed} ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
  })
  return (
    <PullRequestActionButton
      label={
        thread.data?.running || run.data?.already_running
          ? labels.running
          : thread.isPending
            ? labels.checking
            : thread.isError
              ? labels.unavailable
              : run.isPending
                ? labels.queuing
                : run.isSuccess
                  ? labels.queued
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
      onClick={() => run.mutate()}
      errors={[thread.error, run.error]}
    />
  )
}
