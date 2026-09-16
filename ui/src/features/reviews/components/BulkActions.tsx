import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { forgetPullRequest, refreshPullRequest } from "../lib/cache"
import { writePreferredMergeMethod } from "../lib/mergeMethod"
import { isFixable, isMergeable, pullRequestKey } from "../lib/status"
import { BulkMergeDialog } from "./BulkMergeDialog"
import { ConfirmCloseDialog } from "./ConfirmCloseDialog"

type BulkAction = "close" | "fix" | "merge" | "ready"

interface BulkRequest {
  action: BulkAction
  pullRequests: OpenPullRequest[]
  method?: MergeMethod
}

interface BulkOutcome {
  action: BulkAction
  succeeded: OpenPullRequest[]
  failures: Array<{ key: string; message: string }>
}

async function runBulkAction(
  action: BulkAction,
  pr: OpenPullRequest,
  method: MergeMethod | undefined
) {
  if (action === "close") {
    const result = await api.closePullRequest(pr)
    if (!result.closed) throw new Error("GitHub did not confirm the close.")
  } else if (action === "ready") {
    const result = await api.markPullRequestReady(pr)
    if (!result.ready)
      throw new Error("GitHub did not confirm the ready for review.")
  } else if (action === "merge") {
    if (!method) throw new Error("Choose a merge method.")
    const result = await api.mergePullRequest(pr, method)
    if (!result.merged) throw new Error("GitHub did not confirm the merge.")
  } else {
    await api.fixPullRequest(pr)
  }
}

function outcomeMessage({ action, succeeded, failures }: BulkOutcome) {
  const total = succeeded.length + failures.length
  const counted =
    failures.length === 0
      ? `${total} pull request${total === 1 ? "" : "s"}`
      : `${succeeded.length} of ${total} pull request${total === 1 ? "" : "s"}`
  if (action === "close") return `Closed ${counted}`
  if (action === "merge") return `Merged ${counted}`
  if (action === "ready") return `Marked ${counted} ready for review`
  return `Queued fixes for ${counted}`
}

export function BulkActions({
  selected,
  login,
  onClear,
  onSettled,
}: {
  selected: OpenPullRequest[]
  login: string
  onClear: () => void
  onSettled: (succeeded: OpenPullRequest[]) => void
}) {
  const queryClient = useQueryClient()
  const [prompt, setPrompt] = useState<"close" | "merge" | null>(null)
  const bulk = useMutation({
    mutationFn: async ({
      action,
      pullRequests,
      method,
    }: BulkRequest): Promise<BulkOutcome> => {
      const succeeded: OpenPullRequest[] = []
      const failures: BulkOutcome["failures"] = []
      for (const pr of pullRequests) {
        try {
          await runBulkAction(action, pr, method)
          succeeded.push(pr)
        } catch (error) {
          failures.push({
            key: pullRequestKey(pr),
            message: error instanceof Error ? error.message : String(error),
          })
        }
      }
      return { action, succeeded, failures }
    },
    onSuccess: (outcome) => {
      const { action, succeeded, failures } = outcome
      if (action === "fix")
        void queryClient.invalidateQueries({
          queryKey: ["pr-thread-status", login],
        })
      else if (action === "ready")
        for (const pr of succeeded) refreshPullRequest(queryClient, login, pr)
      else for (const pr of succeeded) forgetPullRequest(queryClient, login, pr)
      onSettled(succeeded)
      const message = outcomeMessage(outcome)
      if (failures.length === 0) toast.success(message)
      else
        toast.error(message, {
          description: failures
            .map((failure) => `${failure.key}: ${failure.message}`)
            .join("\n"),
        })
    },
    retry: false,
  })
  const active = bulk.isPending ? bulk.variables.action : null
  const fixable = selected.every((pr) => isFixable(pr) && !pr.detailsLoading)
  const mergeable = selected.every(isMergeable)
  const drafts = selected.every((pr) => pr.draft === true)
  return (
    <div
      role="group"
      aria-label="Bulk pull request actions"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs"
    >
      <span aria-live="polite" className="font-medium">
        {selected.length} selected
      </span>
      <Button size="sm" variant="ghost" onClick={onClear}>
        Clear selection
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending}
        onClick={() => setPrompt("close")}
      >
        {active === "close" ? "Closing…" : "Close"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !fixable}
        title={
          fixable
            ? undefined
            : "Every selected PR must be conflicted or failing"
        }
        onClick={() => bulk.mutate({ action: "fix", pullRequests: selected })}
      >
        {active === "fix" ? "Queuing fixes…" : "Fix"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !mergeable}
        title={
          mergeable
            ? undefined
            : "Every selected PR must be approved and passing"
        }
        onClick={() => setPrompt("merge")}
      >
        {active === "merge" ? "Merging…" : "Merge"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !drafts}
        title={drafts ? undefined : "Every selected PR must be a draft"}
        onClick={() => bulk.mutate({ action: "ready", pullRequests: selected })}
      >
        {active === "ready" ? "Marking ready…" : "Mark ready"}
      </Button>
      {prompt === "close" && (
        <ConfirmCloseDialog
          pullRequests={selected}
          onCancel={() => setPrompt(null)}
          onConfirm={() => {
            setPrompt(null)
            bulk.mutate({ action: "close", pullRequests: selected })
          }}
        />
      )}
      {prompt === "merge" && (
        <BulkMergeDialog
          pullRequests={selected}
          onCancel={() => setPrompt(null)}
          onConfirm={(method) => {
            setPrompt(null)
            writePreferredMergeMethod(method)
            bulk.mutate({ action: "merge", pullRequests: selected, method })
          }}
        />
      )}
    </div>
  )
}
