import { Link } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ClockCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ClockCounterClockwise"
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
  limit = 100,
}: {
  automationId?: string
  limit?: number
}) {
  const runsQuery = useThreadsPage({
    limit,
    offset: 0,
    scope: "automation",
    automationId,
  })
  const runs = runsQuery.data?.items ?? []
  const grouped = groupAutomationRuns(runs)

  if (runsQuery.isLoading) {
    return (
      <div
        role="status"
        className="flex items-center justify-center gap-space-2 rounded-xl border border-dashed border-default px-6 py-12 text-xs text-secondary"
      >
        <Spinner size="xs" />
        Loading automation runs…
      </div>
    )
  }
  if (runsQuery.isError) {
    return (
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
  }
  if (runs.length === 0) {
    return (
      <EmptyState
        icon={ClockCounterClockwiseIcon}
        title="No automation runs yet."
        className="rounded-xl border border-dashed border-default"
      />
    )
  }

  return (
    <div className="space-y-6">
      {grouped.map((group) => (
        <section key={group.id}>
          {!automationId && (
            <div className="mb-2 flex items-center gap-2 px-1">
              <h2 className="text-xs font-medium text-primary">{group.name}</h2>
              <span className="text-xxs text-secondary">
                {group.runs.length}
              </span>
            </div>
          )}
          <div className="space-y-2">
            {group.runs.map((run) => (
              <AutomationRunRow key={run.id} run={run} />
            ))}
          </div>
        </section>
      ))}
      {runsQuery.data?.hasMore && (
        <p className="text-center text-xs text-secondary">
          Showing the {limit} most recent runs.
        </p>
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
      className="flex items-center gap-3 rounded-xl border border-default bg-surface-level-1 px-4 py-3 transition-colors hover:bg-surface-level-1-hover"
    >
      {run.status === "running" ? (
        <Spinner size="xs" className="shrink-0 text-icon-brand" />
      ) : (
        <span
          className={cn(
            "size-2.5 shrink-0 rounded-full",
            run.status === "error" || run.status === "interrupted"
              ? "bg-error-strong"
              : run.status === "finished"
                ? "bg-success-strong"
                : "bg-surface-level-4"
          )}
        />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-primary">{run.title}</p>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-tertiary">
          <span>{STATUS_LABELS[run.status]}</span>
          <span>{isTest ? "Test run" : "Scheduled run"}</span>
          {run.automationActionPosted && (
            <span
              className="flex items-center gap-1 text-success-secondary"
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
