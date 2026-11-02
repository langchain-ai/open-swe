import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link as RouterLink } from "@tanstack/react-router"
import { useEffect, useId, useRef, useState } from "react"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { ListNumbersIcon } from "@phosphor-icons/react/dist/ssr/ListNumbers"
import { toast } from "sonner"

import {
  api,
  type ReviewDetail,
  type ScoutProgress,
  type ScoutStatus,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { Button } from "@langchain/macaw-components/Button"
import { Link } from "@langchain/macaw-components/Link"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { agentThreadKeys } from "@/features/agents/lib/queries"
import { reviewQueries, type PullRequestRef } from "./queries"
import { plural } from "./text"

const RUNNING: ScoutStatus = { state: "running", head_sha: null, error: null }

interface ScoutNotice {
  /** `warning` when nothing broke and building again is all it takes. */
  tone: "error" | "warning"
  /** The toast shown when a run the page watched ends. */
  title: string
  detail: string
  /** The line the callout keeps showing afterwards. */
  inline: string
}

/** What to tell people about a scout that is not running, or null when there is nothing to say. */
function scoutNotice(status: ScoutStatus | null): ScoutNotice | null {
  switch (status?.state) {
    case "failed": {
      const summary =
        status.error?.split("\n", 1)[0]?.slice(0, 300) || "unknown error"
      return {
        tone: "error",
        title: "The walkthrough failed to build",
        detail: `The review scout crashed: ${summary}`,
        inline: `Last attempt failed: ${summary}`,
      }
    }
    case "outdated": {
      const commit = status.head_sha?.slice(0, 7) ?? "an older commit"
      return {
        tone: "warning",
        title: "A new commit landed during the build",
        detail: `The review scout is still on ${commit}, so it won't plan the latest commit. Build again.`,
        inline: `A new commit landed while the scout was on ${commit}. Build again for the latest commit.`,
      }
    }
    case "finished":
      return {
        tone: "error",
        title: "The walkthrough failed to build",
        detail:
          "The review scout finished without a plan. Its thread and LangSmith trace show why.",
        inline:
          "Last attempt finished without a plan. Its thread and LangSmith trace show why.",
      }
    default:
      return null
  }
}

/** Runs the review scout alone, so a reading order exists without a full review. */
export function WalkthroughCallout({
  pr,
  detail,
}: {
  pr: PullRequestRef
  detail: ReviewDetail
}) {
  const queryClient = useQueryClient()
  const key = reviewQueries.detail(pr).queryKey
  const scout = useMutation({
    mutationFn: () => api.runReviewScout(pr.owner, pr.repo, pr.number),
    meta: { errorTitle: "Couldn't build walkthrough" },
    onSuccess: ({ started }) => {
      if (started)
        queryClient.setQueryData(key, (old) =>
          old ? { ...old, walkthrough_scout: RUNNING } : old
        )
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.lists })
      void queryClient.invalidateQueries({ queryKey: key })
    },
  })
  const status = detail.walkthrough_scout
  const scoutRunning = status?.state === "running"
  const running = scoutRunning || scout.isPending
  const notice = running ? null : scoutNotice(status)
  const noticeTone = notice?.tone
  const noticeTitle = notice?.title
  const noticeDetail = notice?.detail
  const wasRunning = useRef(scoutRunning)
  useEffect(() => {
    if (wasRunning.current && !scoutRunning && noticeTone && noticeTitle) {
      toast[noticeTone](noticeTitle, { description: noticeDetail })
    }
    wasRunning.current = scoutRunning
  }, [scoutRunning, noticeTone, noticeTitle, noticeDetail])
  useEffect(() => {
    if (!noticeDetail) return
    console.error("Review scout ended without a walkthrough", {
      pr: `${pr.owner}/${pr.repo}#${pr.number}`,
      headSha: detail.head_sha,
      scoutThreadId: detail.walkthrough_scout_thread_id,
      traceUrl: detail.walkthrough_trace_url,
      detail: noticeDetail,
    })
  }, [
    noticeDetail,
    pr,
    detail.head_sha,
    detail.walkthrough_scout_thread_id,
    detail.walkthrough_trace_url,
  ])

  return (
    <div className="flex items-start gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3">
      <ListNumbersIcon
        className="mt-0.5 size-4 shrink-0 text-brand-primary"
        weight="regular"
      />
      <div className="min-w-0 flex-1 text-xs">
        <p className="text-xs font-medium text-primary">
          {running ? "Building the walkthrough…" : "Read this PR step by step"}
        </p>
        <p className="mt-0.5 text-secondary">
          {running
            ? "Open SWE is ordering the changes into narrated steps. This takes a few minutes; the page updates on its own."
            : "Open SWE orders the changes into narrated steps and moves mechanical edits to the end."}
        </p>
        {running && detail.walkthrough_progress && (
          <ScoutProgressPreview progress={detail.walkthrough_progress} />
        )}
        {notice && (
          <p
            className={cn(
              "mt-space-1 break-words",
              notice.tone === "warning"
                ? "text-warning-secondary"
                : "text-error-secondary"
            )}
          >
            {notice.inline}
          </p>
        )}
        {(running || notice) && (
          <div className="mt-space-1 flex flex-wrap gap-space-3">
            {detail.walkthrough_scout_thread_id && (
              <Link
                as={
                  <RouterLink
                    to="/agents/$threadId"
                    params={{ threadId: detail.walkthrough_scout_thread_id }}
                  />
                }
                variant="xs"
              >
                Open thread
              </Link>
            )}
            {detail.walkthrough_trace_url && (
              <Link
                href={detail.walkthrough_trace_url}
                target="_blank"
                rel="noopener noreferrer"
                variant="xs"
                rightDecorator={ArrowUpRightIcon}
              >
                LangSmith trace
              </Link>
            )}
          </div>
        )}
      </div>
      <Button
        size="sm"
        color="secondary"
        variant="outlined"
        leftDecorator={ListNumbersIcon}
        loading={running}
        disabled={running}
        onClick={() => scout.mutate()}
      >
        {running ? "Building…" : "Build walkthrough"}
      </Button>
    </div>
  )
}

function ScoutProgressPreview({ progress }: { progress: ScoutProgress }) {
  const { recent } = progress
  const [expanded, setExpanded] = useState(false)
  const activityId = useId()
  const summary = `${plural(progress.steps, "step")} committed`
  return (
    <div className="mt-space-2 text-secondary">
      {recent.length > 0 ? (
        <Button
          size="sm"
          color="secondary"
          variant="plain"
          leftDecorator={expanded ? CaretDownIcon : CaretRightIcon}
          aria-label={`${summary}, walkthrough activity`}
          aria-expanded={expanded}
          aria-controls={activityId}
          onClick={() => setExpanded(!expanded)}
        >
          {summary}
        </Button>
      ) : (
        <p>{summary}</p>
      )}
      {recent.length > 0 && (
        <ol
          id={activityId}
          hidden={!expanded}
          className="mt-space-1 space-y-0.5 font-mono text-xxs"
        >
          {recent.map((action, index) => {
            const current = progress.running && index === recent.length - 1
            return (
              <li
                key={index}
                className={cn(
                  "flex min-w-0 gap-space-2",
                  current && "text-primary"
                )}
              >
                <span className="shrink-0">
                  {current ? (
                    <Spinner size="xxs" className="inline-block" />
                  ) : (
                    "·"
                  )}
                </span>
                <span className="shrink-0">{action.tool}</span>
                {action.target && (
                  <span className="min-w-0 truncate">{action.target}</span>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
