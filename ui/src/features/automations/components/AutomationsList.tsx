import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArrowSquareOutIcon,
  ClockIcon,
  LightningIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  StackIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import type { AgentSchedule } from "@/features/agents/lib/types"
import { AutomationRuns } from "@/features/automations/components/AutomationRuns"
import { AutomationTemplates } from "@/features/automations/components/AutomationTemplates"
import { buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { describeTriggers } from "@/features/automations/lib/triggers"
import {
  agentMutationKeys,
  useAgentSchedules,
  useTriggerAgentSchedule,
  useUpdateAgentSchedule,
  useWorkspaceOptions,
} from "@/features/agents/lib/queries"
import { usePendingVariables } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
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

export type AutomationsTab = "overview" | "runs"

export function AutomationsList({
  tab,
  onTabChange,
}: {
  tab: AutomationsTab
  onTabChange: (tab: AutomationsTab) => void
}) {
  const schedulesQuery = useAgentSchedules()
  const canManage = useSession().data?.is_admin === true
  const schedules = schedulesQuery.data ?? []

  const total = schedules.length
  const active = schedules.filter((schedule) => schedule.enabled).length
  const paused = total - active
  const issues = schedules.filter((schedule) => !!schedule.lastError).length

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8 max-md:pt-16">
        <h1 className="text-base font-medium text-primary">Automations</h1>
        <p className="mt-1 text-xs text-secondary">
          Run Open SWE on a schedule or on GitHub, Slack, and Linear events.
          Each run starts a fresh agent thread.{" "}
          {!canManage && "Workspace admins manage automation setup."}
        </p>
        {canManage && (
          <p className="mt-3 rounded-lg border border-default bg-surface-level-1 px-3 py-2 text-xs text-secondary">
            Automations can also be listed and managed through Open SWE. Start a
            new thread, turn on Admin next to the model picker, then ask the
            agent to make the change.
          </p>
        )}
        <div className="mt-4 flex w-fit rounded-md border border-default bg-surface-level-1 p-0.5">
          {(["overview", "runs"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => onTabChange(value)}
              className={cn(
                "rounded px-3 py-1 text-xs capitalize transition-colors",
                tab === value
                  ? "bg-surface-level-1-hover text-primary"
                  : "text-secondary hover:text-primary"
              )}
            >
              {value}
            </button>
          ))}
        </div>

        {tab === "runs" ? (
          <div className="mt-6">
            <AutomationRuns />
          </div>
        ) : (
          <>
            <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatCard label="Total" value={total} />
              <StatCard label="Active" value={active} />
              <StatCard label="Paused" value={paused} />
              <StatCard
                label="Needs attention"
                value={issues}
                highlight={issues > 0}
              />
            </div>

            <div className="mt-8 flex items-center justify-between">
              <span className="text-xs font-medium text-secondary">
                {total} {total === 1 ? "automation" : "automations"}
              </span>
              {canManage && (
                <Link to="/agents/automations/new" className={buttonVariants()}>
                  <PlusIcon className="size-4" />
                  New Automation
                </Link>
              )}
            </div>

            <div className="mt-3">
              {schedulesQuery.isLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full rounded-xl" />
                  <Skeleton className="h-16 w-full rounded-xl" />
                </div>
              ) : total === 0 ? (
                <EmptyState canManage={canManage} />
              ) : (
                <div className="space-y-2">
                  {schedules.map((schedule) => (
                    <AutomationRow
                      key={schedule.id}
                      schedule={schedule}
                      canManage={canManage}
                    />
                  ))}
                </div>
              )}
            </div>

            {canManage && <AutomationTemplates />}
          </>
        )}
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
    <div className="rounded-xl border border-default bg-surface-level-1 px-4 py-3">
      <div className="text-xs text-tertiary">{label}</div>
      <div
        className={cn(
          "mt-1 text-lg font-medium",
          highlight ? "text-error-secondary" : "text-primary"
        )}
      >
        {value}
      </div>
    </div>
  )
}

function EmptyState({ canManage }: { canManage: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-default bg-surface-level-1 px-6 py-14 text-center">
      <div className="rounded-full bg-surface-level-1-hover p-3 text-secondary">
        <LightningIcon className="size-5" />
      </div>
      <h3 className="mt-4 text-sm font-medium text-primary">
        No automations yet
      </h3>
      <p className="mt-1 max-w-sm text-xs text-secondary">
        Run Open SWE on a schedule or on GitHub, Slack, and Linear events —
        review code, triage new issues, or investigate an alert.
      </p>
      {canManage && (
        <Link
          to="/agents/automations/new"
          className={cn(buttonVariants(), "mt-4")}
        >
          <PlusIcon className="size-4" />
          New Automation
        </Link>
      )}
    </div>
  )
}

function AutomationRow({
  schedule,
  canManage,
}: {
  schedule: AgentSchedule
  canManage: boolean
}) {
  const navigate = useNavigate()
  const updateSchedule = useUpdateAgentSchedule()
  const triggerSchedule = useTriggerAgentSchedule()
  const workspaces = useWorkspaceOptions().data?.workspaces ?? []
  const workspaceName =
    workspaces.find((option) => option.slug === schedule.workspace)?.name ??
    schedule.workspace
  const isToggling = usePendingVariables<{ scheduleId: string }>(
    agentMutationKeys.updateSchedule
  ).some((vars) => vars.scheduleId === schedule.id)
  const isTesting =
    triggerSchedule.isPending && triggerSchedule.variables === schedule.id

  const onTest = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isTesting || isToggling) return
    triggerSchedule.mutate(schedule.id, {
      onSuccess: (result) => {
        void navigate({
          to: "/agents/$threadId",
          params: { threadId: result.thread_id },
        })
      },
    })
  }

  const onToggle = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isTesting || isToggling) return
    updateSchedule.mutate({
      scheduleId: schedule.id,
      body: { enabled: !schedule.enabled },
    })
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-default bg-surface-level-1 px-4 py-3 transition-colors hover:border-strong">
      <Link
        to="/agents/automations/$scheduleId"
        params={{ scheduleId: schedule.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            schedule.enabled ? "bg-success-strong" : "bg-[color:var(--border-default)]"
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-primary">
              {schedule.name}
            </span>
            {schedule.lastError && (
              <WarningCircleIcon
                className="size-3.5 shrink-0 text-error-secondary"
                aria-label="Last run failed"
              >
                <title>Last run failed</title>
              </WarningCircleIcon>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-tertiary">
            <span className="flex items-center gap-1">
              <ClockIcon className="size-3.5" />
              {describeTriggers(schedule)}
            </span>
            {workspaces.length > 1 && (
              <span className="flex items-center gap-1">
                <StackIcon className="size-3.5" />
                {workspaceName}
              </span>
            )}
            <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
          </div>
        </div>
      </Link>
      {schedule.lastThreadId && (
        <Link
          to="/agents/$threadId"
          params={{ threadId: schedule.lastThreadId }}
          aria-label="Open latest automation run"
          className="shrink-0 rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-level-1-hover hover:text-primary"
        >
          <ArrowSquareOutIcon className="size-4" />
        </Link>
      )}
      {canManage && (
        <>
          <button
            type="button"
            onClick={onTest}
            disabled={isTesting || isToggling}
            aria-label="Test automation"
            className="flex shrink-0 items-center gap-1 rounded-md border border-default px-2 py-1 text-xs text-secondary transition-colors hover:bg-surface-level-1-hover hover:text-primary disabled:opacity-40"
          >
            <LightningIcon className="size-3.5" />
            {isTesting ? "Starting…" : "Test"}
          </button>
          <button
            type="button"
            onClick={onToggle}
            disabled={isTesting || isToggling}
            aria-label={
              schedule.enabled ? "Pause automation" : "Resume automation"
            }
            className="shrink-0 rounded-md p-1.5 text-tertiary transition-colors hover:bg-surface-level-1-hover hover:text-primary disabled:opacity-40"
          >
            {schedule.enabled ? (
              <PauseIcon className="size-4" />
            ) : (
              <PlayIcon className="size-4" />
            )}
          </button>
        </>
      )}
    </div>
  )
}
