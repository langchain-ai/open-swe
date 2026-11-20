import { useState } from "react"
import type { ReactNode } from "react"
import { ClockCounterClockwiseRegularIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { Link } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Skeleton } from "@/components/Skeleton"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { cn, formatRelativeTime } from "@/lib/utils"

const STATUS_LABELS: Record<AgentStatus, string> = {
  running: "Running",
  finished: "Finished",
  interrupted: "Interrupted",
  error: "Error",
  idle: "Idle",
}

export function AutomationRuns({
  automationId,
  limit = 25,
}: {
  automationId?: string
  limit?: number
}) {
  const [offset, setOffset] = useState(0)
  const runsQuery = useThreadsPage({
    limit,
    offset,
    scope: "automation",
    automationId,
  })
  const runs = runsQuery.data?.items ?? []
  const grouped = groupAutomationRuns(runs)

  let content: ReactNode
  if (runsQuery.isLoading) {
    content = (
      <div
        role="status"
        aria-label="Loading automation runs"
        className="space-y-space-2"
      >
        {Array.from({ length: 4 }, (_, index) => (
          <div
            key={index}
            className="flex items-center gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3"
          >
            <Skeleton className="size-2.5 shrink-0 rounded-full" />
            <div className="min-w-0 flex-1 space-y-space-2 py-space-1">
              <Skeleton className="h-3.5 w-64 max-w-full" />
              <Skeleton className="h-3 w-40 max-w-full" />
            </div>
          </div>
        ))}
      </div>
    )
  } else if (runsQuery.isError) {
    content = (
      <Banner
        intent="error"
        action={
          <Button
            color="secondary"
            variant="outlined"
            onClick={() => void runsQuery.refetch()}
            disabled={runsQuery.isFetching}
          >
            {runsQuery.isFetching ? "Retrying…" : "Retry"}
          </Button>
        }
      >
        Automation runs could not be loaded.
      </Banner>
    )
  } else if (runs.length === 0) {
    content = (
      <EmptyState
        icon={ClockCounterClockwiseRegularIcon}
        title={
          offset === 0 ? "No automation runs yet." : "No runs on this page."
        }
        className="rounded-xl border border-dashed border-default"
      />
    )
  } else {
    content = grouped.map((group) => (
      <section key={group.id}>
        {!automationId && (
          <div className="mb-space-2 flex items-center gap-space-2 px-space-1">
            <Text as="h2" variant="sm" weight="medium" color="primary">
              {group.name}
            </Text>
            <span className="text-xxs text-secondary">{group.runs.length}</span>
          </div>
        )}
        <div className="space-y-space-2">
          {group.runs.map((run) => (
            <AutomationRunRow key={run.id} run={run} />
          ))}
        </div>
      </section>
    ))
  }

  return (
    <div className="space-y-space-5" aria-busy={runsQuery.isFetching}>
      {!runsQuery.isLoading &&
        !runsQuery.isError &&
        runs.some((run) => run.status === "finished") && (
          <p className="text-xs text-secondary">
            Finished means the run ended, not that its task succeeded. Open a
            run to review the outcome.
          </p>
        )}
      {content}
      {(offset > 0 || runsQuery.data?.hasMore) && (
        <nav
          aria-label="Automation run pages"
          className="flex flex-wrap items-center justify-between gap-space-3 text-xs text-secondary"
        >
          <span>
            {offset === 0
              ? `Showing the ${limit} most recent runs.`
              : "Older automation runs."}
          </span>
          <div className="flex items-center gap-space-2">
            <Button
              color="secondary"
              variant="outlined"
              disabled={offset === 0 || runsQuery.isFetching}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Previous
            </Button>
            <span aria-live="polite">Page {offset / limit + 1}</span>
            <Button
              color="secondary"
              variant="outlined"
              disabled={
                !runsQuery.data?.hasMore ||
                runsQuery.isFetching ||
                runsQuery.isError
              }
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </div>
        </nav>
      )}
    </div>
  )
}

function groupAutomationRuns(runs: Array<AgentThread>) {
  const groups = new Map<
    string,
    { id: string; name: string; runs: Array<AgentThread> }
  >()
  for (const run of runs) {
    const id = run.automationId || "unknown"
    const current = groups.get(id) ?? {
      id,
      name: run.automationName || "Unknown automation",
      runs: [],
    }
    current.runs.push(run)
    groups.set(id, current)
  }
  return [...groups.values()].sort(
    (left, right) =>
      (right.runs[0]?.updatedAt ?? 0) - (left.runs[0]?.updatedAt ?? 0)
  )
}

function AutomationRunRow({ run }: { run: AgentThread }) {
  const isTest = run.triggerKind === "schedule_test"
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: run.id }}
      className="flex items-center gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3 transition-colors hover:bg-surface-level-1-hover"
    >
      {run.status === "running" ? (
        <Spinner size="xs" className="shrink-0 text-icon-brand" />
      ) : (
        <span
          className={cn(
            "size-2.5 shrink-0 rounded-full",
            run.status === "error" || run.status === "interrupted"
              ? "bg-error-strong"
              : "bg-surface-level-4"
          )}
        />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-primary">{run.title}</p>
        <div className="mt-space-1 flex flex-wrap gap-x-space-3 gap-y-space-1 text-xs text-tertiary">
          <span>{STATUS_LABELS[run.status]}</span>
          <span>{isTest ? "Test run" : "Scheduled run"}</span>
          {run.automationActionPosted && (
            <span
              className="flex items-center gap-space-1 text-success-secondary"
              aria-label="Action posted to Slack"
            >
              <SlackLogoIcon size={14} weight="regular" />
              Posted to Slack
            </span>
          )}
          {run.repoFullName && <span>{run.repoFullName}</span>}
          <span>{formatRelativeTime(run.updatedAt)}</span>
        </div>
      </div>
      <ArrowSquareOutIcon
        size={16}
        weight="regular"
        className="shrink-0 text-icon-tertiary"
      />
    </Link>
  )
}
