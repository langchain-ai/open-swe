import { Button } from "@langchain/macaw-components/Button"
import type { ReviewOutcome } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { repositoryOf, type PullRequestRef } from "../lib/pullRequest"
import {
  errorMessage,
  keys,
  type Inbox,
  type PullRequestRuns,
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
  disabled = false,
  currentHeadSha,
  showOutcome = true,
}: {
  pr: PullRequestRef
  outcome: ReviewOutcome | null
  hasCapture: boolean
  disabled?: boolean
  currentHeadSha?: string
  showOutcome?: boolean
}) {
  const host = usePluginHost()
  const client = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (outcome?.status !== "queued") return
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [outcome?.status])
  const repository = repositoryOf(pr)
  const update = (next: ReviewOutcome) => {
    client.setQueryData<PullRequestRuns>(
      keys.runs(pr),
      (value) => value && { ...value, outcome: next }
    )
    client.setQueriesData<Inbox>(
      { queryKey: keys.inboxPages },
      (value) =>
        value && {
          ...value,
          items: value.items.map((item) =>
            item.repository === repository && item.pull.number === pr.number
              ? { ...item, outcome: next }
              : item
          ),
        }
    )
  }
  const capture = useMutation({
    mutationFn: () => host.github(repository).requestReview(pr.number),
    onMutate: async () => {
      await Promise.all([
        client.cancelQueries({ queryKey: keys.runs(pr) }),
        client.cancelQueries({ queryKey: ["inbox"] }),
      ])
      const previous = client.getQueryData<PullRequestRuns>(keys.runs(pr))
      const inboxes = client.getQueriesData<Inbox>({
        queryKey: keys.inboxPages,
      })
      update({
        id: "requesting",
        head_sha: "",
        status: "queued",
        reason: "Requesting capture…",
        created_at: new Date().toISOString(),
      })
      return { previous, inboxes }
    },
    onSuccess: (result) => update(result),
    onError: (error, _variables, context) => {
      if (context?.previous)
        client.setQueryData(keys.runs(pr), context.previous)
      for (const [key, value] of context?.inboxes ?? [])
        client.setQueryData(key, value)
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
  const labels = {
    queued: "Capture queued",
    blocked: "Capture blocked",
    failed: "Capture failed",
    skipped: "Capture skipped",
  }
  return (
    <div className="flex flex-col items-start gap-space-2">
      {showOutcome && outcome && outcome.status !== "complete" && (
        <p role="status" className="text-xs text-secondary">
          {stalled
            ? "Capture has not reported an outcome"
            : labels[outcome.status]}
          {earlierCommit ? " (earlier commit)" : ""}
          {outcome.reason ? `: ${outcome.reason}` : ""}
        </p>
      )}
      <Button
        size="sm"
        color="secondary"
        variant="outlined"
        loading={capture.isPending}
        disabled={disabled || queued}
        onClick={() => capture.mutate()}
      >
        {queued
          ? "Capture queued"
          : hasCapture || outcome
            ? "Retry capture"
            : "Capture"}
      </Button>
      {capture.isError && (
        <p role="alert" className="text-xs text-error-secondary">
          {errorMessage(capture.error)}
        </p>
      )}
    </div>
  )
}
