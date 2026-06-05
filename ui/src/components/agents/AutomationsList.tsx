import { Link } from "@tanstack/react-router"
import {
  ClockIcon,
  LightningIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import type { AgentSchedule } from "@/lib/agents/types"
import { buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { describeCron } from "@/lib/agents/cron"
import {
  useAgentSchedules,
  useUpdateAgentSchedule,
} from "@/lib/agents/queries"
import { cn } from "@/lib/utils"

function formatDate(value?: string | null): string {
  if (!value) return "Never run"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "Never run"
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

export function AutomationsList() {
  const schedulesQuery = useAgentSchedules()
  const schedules = schedulesQuery.data ?? []

  const total = schedules.length
  const active = schedules.filter((s) => s.enabled).length
  const paused = total - active
  const issues = schedules.filter((s) => !!s.lastError).length

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8 max-md:pt-16">
        <h1 className="text-2xl font-semibold text-[var(--ui-text)]">
          Automations
        </h1>
        <p className="mt-1 text-sm text-[var(--ui-text-muted)]">
          Run Open SWE on a recurring schedule. Each run starts a fresh agent
          thread.
        </p>

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Total" value={total} />
          <StatCard label="Active" value={active} />
          <StatCard label="Paused" value={paused} />
          <StatCard label="Needs attention" value={issues} highlight={issues > 0} />
        </div>

        <div className="mt-8 flex items-center justify-between">
          <span className="text-sm font-medium text-[var(--ui-text-muted)]">
            {total} {total === 1 ? "automation" : "automations"}
          </span>
          <Link to="/agents/automations/new" className={buttonVariants()}>
            <PlusIcon className="size-4" />
            New Automation
          </Link>
        </div>

        <div className="mt-3">
          {schedulesQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full rounded-xl" />
              <Skeleton className="h-16 w-full rounded-xl" />
            </div>
          ) : total === 0 ? (
            <EmptyState />
          ) : (
            <div className="space-y-2">
              {schedules.map((schedule) => (
                <AutomationRow key={schedule.id} schedule={schedule} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  highlight,
}: {
  label: string
  value: number
  highlight?: boolean
}) {
  return (
    <div className="rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3">
      <div className="text-xs text-[var(--ui-text-dim)]">{label}</div>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold",
          highlight ? "text-[var(--ui-danger)]" : "text-[var(--ui-text)]"
        )}
      >
        {value}
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-[var(--ui-border)] bg-[var(--ui-surface)] px-6 py-14 text-center">
      <div className="rounded-full bg-[var(--ui-panel-2)] p-3 text-[var(--ui-text-muted)]">
        <LightningIcon className="size-5" />
      </div>
      <h3 className="mt-4 text-sm font-medium text-[var(--ui-text)]">
        No automations yet
      </h3>
      <p className="mt-1 max-w-sm text-sm text-[var(--ui-text-muted)]">
        Schedule Open SWE to run on a recurring cadence — review code, triage
        issues, or keep docs up to date.
      </p>
      <Link
        to="/agents/automations/new"
        className={cn(buttonVariants(), "mt-4")}
      >
        <PlusIcon className="size-4" />
        New Automation
      </Link>
    </div>
  )
}

function AutomationRow({ schedule }: { schedule: AgentSchedule }) {
  const updateSchedule = useUpdateAgentSchedule()
  const isToggling =
    updateSchedule.isPending &&
    updateSchedule.variables.scheduleId === schedule.id

  const onToggle = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    updateSchedule.mutate({
      scheduleId: schedule.id,
      body: { enabled: !schedule.enabled },
    })
  }

  return (
    <Link
      to="/agents/automations/$scheduleId"
      params={{ scheduleId: schedule.id }}
      className="flex items-center gap-3 rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3 transition-colors hover:border-[var(--ui-text-dim)]"
    >
      <span
        className={cn(
          "size-2 shrink-0 rounded-full",
          schedule.enabled ? "bg-[var(--ui-success)]" : "bg-[var(--ui-border)]"
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-[var(--ui-text)]">
            {schedule.name}
          </span>
          {schedule.lastError && (
            <WarningCircleIcon
              className="size-3.5 shrink-0 text-[var(--ui-danger)]"
              aria-label="Last run failed"
            >
              <title>Last run failed</title>
            </WarningCircleIcon>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--ui-text-dim)]">
          <span className="flex items-center gap-1">
            <ClockIcon className="size-3.5" />
            {describeCron(schedule.schedule)}
          </span>
          {schedule.repo && <span>{schedule.repo}</span>}
          <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onToggle}
        disabled={isToggling}
        aria-label={schedule.enabled ? "Pause automation" : "Resume automation"}
        className="shrink-0 rounded-md p-1.5 text-[var(--ui-text-dim)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)] disabled:opacity-40"
      >
        {schedule.enabled ? (
          <PauseIcon className="size-4" />
        ) : (
          <PlayIcon className="size-4" />
        )}
      </button>
    </Link>
  )
}
