import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CaretDownIcon } from "@phosphor-icons/react"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { Button } from "@/components/ui/button"
import { ButtonGroup } from "@/components/ui/button-group"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import {
  threadActions,
  type PullRequestThreadActionName,
} from "../lib/threadActions"
import { PullRequestActionButton } from "./PullRequestActionButton"

type PullRequestThreadStatus = Awaited<
  ReturnType<typeof api.pullRequestThreadStatus>
>

/** One agent action on a PR's thread: its live label, whether it can start, and how to start it. */
function useThreadAction(
  pr: OpenPullRequest,
  login: string,
  action: PullRequestThreadActionName
) {
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
  return {
    idle: labels.idle,
    label: run.data?.already_running
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
                : labels.idle,
    disabled:
      thread.isPending ||
      thread.isError ||
      thread.data?.running === true ||
      run.isPending ||
      run.isSuccess,
    start: () => run.mutate(pr),
    error: thread.error,
  }
}

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
  const { label, disabled, start, error } = useThreadAction(pr, login, action)
  return (
    <PullRequestActionButton
      label={label}
      disabled={disabled}
      onClick={start}
      errors={[error]}
    />
  )
}

/** Several fixes as one split button: the first is the button, the caret holds the rest. */
export function PullRequestFixActions({
  pr,
  login,
  actions,
}: {
  pr: OpenPullRequest
  login: string
  actions: ReadonlyArray<PullRequestThreadActionName>
}) {
  // Every action's hook runs on every render so the hook order never depends on the PR's state.
  const byName: Record<
    PullRequestThreadActionName,
    ReturnType<typeof useThreadAction>
  > = {
    "fix-conflicts": useThreadAction(pr, login, "fix-conflicts"),
    "fix-checks": useThreadAction(pr, login, "fix-checks"),
    "address-comments": useThreadAction(pr, login, "address-comments"),
  }
  const [first, ...rest] = actions
  if (!first) return null
  const primary = byName[first]
  if (rest.length === 0)
    return (
      <PullRequestActionButton
        label={primary.label}
        disabled={primary.disabled}
        onClick={primary.start}
        errors={[primary.error]}
      />
    )
  return (
    <div>
      <ButtonGroup aria-label={`Fixes for PR #${pr.number}`}>
        <Button
          size="sm"
          variant="outline"
          aria-live="polite"
          disabled={primary.disabled}
          onClick={primary.start}
        >
          {primary.label}
        </Button>
        <Menu>
          <MenuTrigger
            aria-label={`More fixes for PR #${pr.number}`}
            disabled={primary.disabled}
            render={<Button size="sm" variant="outline" className="px-1.5" />}
          >
            <CaretDownIcon />
          </MenuTrigger>
          <MenuPopup align="start">
            {rest.map((name) => (
              <MenuItem
                key={name}
                disabled={byName[name].disabled}
                onClick={byName[name].start}
              >
                {byName[name].idle}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
      </ButtonGroup>
      {primary.error && (
        <p role="alert" className="mt-1 text-destructive">
          {primary.error.message}
        </p>
      )}
    </div>
  )
}
