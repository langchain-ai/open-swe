import { Link } from "@tanstack/react-router"
import { ArrowSquareOutIcon, CircleNotchIcon } from "@phosphor-icons/react"

import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import { Button } from "@/components/ui/button"
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
      <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center text-xs text-muted-foreground">
        Loading automation runs…
      </div>
    )
  }
  if (runsQuery.isError) {
    return (
      <div className="flex flex-col items-center rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
        <p className="text-xs text-destructive">
          Automation runs could not be loaded.
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="mt-3"
          onClick={() => void runsQuery.refetch()}
          disabled={runsQuery.isFetching}
        >
          {runsQuery.isFetching ? "Retrying…" : "Retry"}
        </Button>
      </div>
    )
  }
  if (runs.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center text-xs text-muted-foreground">
        No automation runs yet.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {grouped.map((group) => (
        <section key={group.id}>
          {!automationId && (
            <div className="mb-2 flex items-center gap-2 px-1">
              <h2 className="text-xs font-medium text-foreground">
                {group.name}
              </h2>
              <span className="text-[10px] text-muted-foreground">
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
        <p className="text-center text-xs text-muted-foreground">
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
  const actionLabel =
    run.automationNotificationMode !== "on_action"
      ? null
      : run.automationActionTaken
        ? "Action taken"
        : run.status === "running"
          ? "Awaiting action"
          : "No action"
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: run.id }}
      className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/70"
    >
      {run.status === "running" ? (
        <CircleNotchIcon className="size-4 shrink-0 animate-spin text-primary" />
      ) : (
        <span
          className={cn(
            "size-2.5 shrink-0 rounded-full",
            run.status === "error" || run.status === "interrupted"
              ? "bg-destructive"
              : run.status === "finished"
                ? "bg-success"
                : "bg-border"
          )}
        />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">
          {run.title}
        </p>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground/70">
          <span>{STATUS_LABELS[run.status]}</span>
          <span>{isTest ? "Test run" : "Scheduled run"}</span>
          {actionLabel && (
            <span
              className={cn(actionLabel === "Action taken" && "text-success")}
            >
              {actionLabel}
            </span>
          )}
          {run.repoFullName && <span>{run.repoFullName}</span>}
          <span>{formatRelativeTime(run.updatedAt)}</span>
        </div>
      </div>
      <ArrowSquareOutIcon className="size-4 shrink-0 text-muted-foreground/70" />
    </Link>
  )
}
