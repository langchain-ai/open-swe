import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useRef } from "react"
import { CircleNotchIcon, ListNumbersIcon } from "@phosphor-icons/react"
import { toast } from "sonner"

import { api, type ReviewDetail, type ScoutProgress } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { agentThreadKeys } from "@/features/agents/lib/queries"
import { reviewQueries, type PullRequestRef } from "./queries"

/** Runs the review scout alone, so a reading order exists without a full review. */
export function WalkthroughCallout({ pr, detail }: { pr: PullRequestRef; detail: ReviewDetail }) {
  const queryClient = useQueryClient()
  const key = reviewQueries.detail(pr).queryKey
  const scout = useMutation({
    mutationFn: () => api.runReviewScout(pr.owner, pr.repo, pr.number),
    meta: { errorTitle: "Couldn't build walkthrough" },
    onSuccess: ({ started }) => {
      if (started)
        queryClient.setQueryData(key, (old) => (old ? { ...old, walkthrough_running: true } : old))
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.lists })
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const running = detail.walkthrough_running || scout.isPending
  const failure = running ? null : detail.walkthrough_error
  const failureSummary = failure?.split("\n", 1)[0]?.slice(0, 300)
  const wasRunning = useRef(detail.walkthrough_running)
  useEffect(() => {
    if (wasRunning.current && !detail.walkthrough_running) {
      toast.error("The walkthrough failed to build", {
        description: failureSummary
          ? `The review scout crashed: ${failureSummary}`
          : "The review scout finished without producing steps. Try again, or check its review-scout run in LangSmith.",
      })
    }
    wasRunning.current = detail.walkthrough_running
  }, [detail.walkthrough_running, failureSummary])
  useEffect(() => {
    if (!failure) return
    console.error("Review scout failed", {
      pr: `${pr.owner}/${pr.repo}#${pr.number}`,
      headSha: detail.head_sha,
      scoutThreadId: detail.walkthrough_scout_thread_id,
      error: failure,
    })
  }, [failure, pr, detail.head_sha, detail.walkthrough_scout_thread_id])

  return (
    <div className="flex items-start gap-3 rounded-xl border border-border bg-card px-4 py-3">
      <ListNumbersIcon className="mt-0.5 size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1 text-xs">
        <p className="text-[13px] font-medium text-foreground">
          {running ? "Building the walkthrough…" : "Read this PR step by step"}
        </p>
        <p className="mt-0.5 text-muted-foreground">
          {running
            ? "Open SWE is ordering the changes into narrated steps. This takes a few minutes; the page updates on its own."
            : "Open SWE orders the changes into narrated steps and moves mechanical edits to the end."}
        </p>
        {running && detail.walkthrough_progress && <ScoutProgressPreview progress={detail.walkthrough_progress} />}
        {failureSummary && (
          <p className="mt-1.5 break-words text-destructive">Last attempt failed: {failureSummary}</p>
        )}
        {(running || failure) && detail.walkthrough_scout_thread_id && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: detail.walkthrough_scout_thread_id }}
            className="mt-1.5 inline-block text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Open thread
          </Link>
        )}
      </div>
      <Button size="sm" variant="outline" onClick={() => scout.mutate()} disabled={running}>
        {running ? <CircleNotchIcon className="animate-spin" /> : <ListNumbersIcon />}
        {running ? "Building…" : "Build walkthrough"}
      </Button>
    </div>
  )
}

function ScoutProgressPreview({ progress }: { progress: ScoutProgress }) {
  const { recent } = progress
  return (
    <div className="mt-2 text-muted-foreground">
      <p>
        {progress.steps} step{progress.steps === 1 ? "" : "s"} committed
      </p>
      {recent.length > 0 && (
        <ol className="mt-1 space-y-0.5 font-mono text-[11px]">
          {recent.map((action, index) => {
            const current = progress.running && index === recent.length - 1
            return (
              <li key={index} className={cn("flex min-w-0 gap-2", current && "text-foreground")}>
                <span className="shrink-0">
                  {current ? <CircleNotchIcon className="inline size-3 animate-spin" /> : "·"}
                </span>
                <span className="shrink-0">{action.tool}</span>
                {action.target && <span className="min-w-0 truncate">{action.target}</span>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
