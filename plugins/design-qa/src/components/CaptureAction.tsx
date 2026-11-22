import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import type { ReviewOutcome } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState, type ReactNode } from "react"

import { repositoryOf, type PullRequestRef } from "../lib/pullRequest"
import { Usage } from "../lib/usage"
import { CaptureAttempt, type FrontendReviewRun } from "../lib/capture"
import {
  errorMessage,
  keys,
  type PullRequestRuns,
  type RepositoryCaptures,
} from "../lib/queries"

/**
 * Capture is requested through the host, which checks app and repository
 * access. The latest attempt's outcome shows only when it didn't capture, and
 * not at all without `showOutcome`.
 */
export function CaptureAction({
  pr,
  outcome,
  hasCapture,
  run,
  disabled = false,
  currentHeadSha,
  showOutcome = true,
  children,
}: {
  pr: PullRequestRef
  outcome: ReviewOutcome | null
  hasCapture: boolean
  run?: FrontendReviewRun | null
  disabled?: boolean
  currentHeadSha?: string
  showOutcome?: boolean
  children?: ReactNode
}) {
  const host = usePluginHost()
  const client = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (
      outcome?.status !== "queued" &&
      outcome?.status !== "blocked" &&
      run?.status !== "blocked"
    )
      return
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [outcome?.status, run?.status])
  const repository = repositoryOf(pr)
  const update = (next: ReviewOutcome) => {
    client.setQueryData<PullRequestRuns>(
      keys.runs(pr),
      (value) => value && { ...value, outcome: next }
    )
    client.setQueryData<RepositoryCaptures>(
      keys.repositoryCaptures(repository),
      (value) =>
        value && {
          ...value,
          outcomes: new Map(value.outcomes).set(pr.number, next),
        }
    )
  }
  const capture = useMutation({
    mutationFn: () => host.github(repository).requestReview(pr.number),
    onMutate: async () => {
      await Promise.all([
        client.cancelQueries({ queryKey: keys.runs(pr) }),
        client.cancelQueries({
          queryKey: keys.repositoryCaptures(repository),
        }),
      ])
      const previous = client.getQueryData<PullRequestRuns>(keys.runs(pr))
      const captures = client.getQueryData<RepositoryCaptures>(
        keys.repositoryCaptures(repository)
      )
      update({
        id: "requesting",
        head_sha: "",
        status: "queued",
        reason: "Requesting capture…",
        created_at: new Date().toISOString(),
      })
      return { previous, captures }
    },
    onSuccess: (result) => update(result),
    onError: (error, _variables, context) => {
      if (context?.previous)
        client.setQueryData(keys.runs(pr), context.previous)
      if (context?.captures)
        client.setQueryData(
          keys.repositoryCaptures(repository),
          context.captures
        )
      void host.ui.toast({
        title: "Could not request capture",
        description: errorMessage(error),
        intent: "error",
      })
    },
  })
  const earlierCommit = Boolean(
    currentHeadSha && outcome?.head_sha && outcome.head_sha !== currentHeadSha
  )
  const stalled = Boolean(
    outcome?.status === "queued" &&
    now - Date.parse(outcome.created_at) > 90 * 60_000
  )
  const queued = outcome?.status === "queued" && !earlierCommit && !stalled
  const attempt = new CaptureAttempt(outcome, now)
  const currentRun = run && (!currentHeadSha || run.head_sha === currentHeadSha)
  const previewPending =
    (!earlierCommit &&
      attempt.previewPending &&
      (!currentRun ||
        !outcome ||
        Date.parse(run.created_at) < Date.parse(outcome.created_at))) ||
    Boolean(
      currentRun &&
      run.status === "blocked" &&
      now - Date.parse(run.created_at) < 90 * 60_000 &&
      (!outcome ||
        Date.parse(run.created_at) >= Date.parse(outcome.created_at)) &&
      CaptureAttempt.isPreviewPending(run.error)
    )
  const capturing =
    currentRun && (run.status === "pending" || run.status === "running")
  const request = () => {
    Usage.record(host, "capture_requested")
    capture.mutate()
  }
  return (
    <div className="flex max-w-full flex-col items-start gap-space-2">
      {showOutcome && outcome && outcome.status !== "complete" && (
        <p role="status" className="text-xs text-secondary">
          {stalled ? "Capture has not reported an outcome" : attempt.label}
          {earlierCommit ? " (earlier commit)" : ""}
          {outcome.reason ? `: ${outcome.reason}` : ""}
        </p>
      )}
      <div className="flex max-w-full flex-wrap items-center gap-space-3">
        {previewPending || capturing || queued ? (
          <p role="status" className="text-xs text-secondary">
            {previewPending
              ? "Waiting for the preview to finish deploying."
              : capturing
                ? "Capturing screenshots…"
                : "Capture queued"}
          </p>
        ) : hasCapture || outcome ? (
          <IconButton
            size="sm"
            color="secondary"
            variant="outlined"
            icon={ArrowClockwiseIcon}
            label="Retry capture"
            loading={capture.isPending}
            disabled={disabled}
            onClick={request}
          />
        ) : (
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            loading={capture.isPending}
            disabled={disabled}
            onClick={request}
          >
            Capture
          </Button>
        )}
        {children}
      </div>
      {capture.isError && (
        <p role="alert" className="text-xs text-error-secondary">
          {errorMessage(capture.error)}
        </p>
      )}
    </div>
  )
}
