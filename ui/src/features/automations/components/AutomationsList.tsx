import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArrowSquareOutIcon,
  ClockIcon,
  LightningIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import type { AgentSchedule } from "@/features/agents/lib/types"
import { AutomationRuns } from "@/features/automations/components/AutomationRuns"
import { AutomationTemplates } from "@/features/automations/components/AutomationTemplates"
import { buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { describeCron } from "@/features/automations/lib/cron"
import {
  useAgentSchedules,
  useTriggerAgentSchedule,
  useUpdateAgentSchedule,
} from "@/features/agents/lib/queries"
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
        <h1 className="text-base font-medium text-foreground">Automations</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Run Open SWE on a recurring schedule. Each run starts a fresh agent
          thread. {!canManage && "Workspace admins manage automation setup."}
        </p>
        <div className="mt-4 flex w-fit rounded-md border border-border bg-card p-0.5">
          {(["overview", "runs"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => onTabChange(value)}
              className={cn(
                "rounded px-3 py-1 text-xs capitalize transition-colors",
                tab === value
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground"
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
              <span className="text-xs font-medium text-muted-foreground">
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
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <div className="text-xs text-muted-foreground/70">{label}</div>
      <div
        className={cn(
          "mt-1 text-lg font-medium",
          highlight ? "text-destructive" : "text-foreground"
        )}
      >
        {value}
      </div>
    </div>
  )
}

function EmptyState({ canManage }: { canManage: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-6 py-14 text-center">
      <div className="rounded-full bg-accent p-3 text-muted-foreground">
        <LightningIcon className="size-5" />
      </div>
      <h3 className="mt-4 text-sm font-medium text-foreground">
        No automations yet
      </h3>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
        Schedule Open SWE to run on a recurring cadence — review code, triage
        issues, or keep docs up to date.
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
  const isToggling =
    updateSchedule.isPending &&
    updateSchedule.variables.scheduleId === schedule.id
  const isTesting =
    triggerSchedule.isPending && triggerSchedule.variables === schedule.id
  const testError =
    triggerSchedule.isError && triggerSchedule.variables === schedule.id
      ? triggerSchedule.error.message
      : null

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
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/70">
      <Link
        to="/agents/automations/$scheduleId"
        params={{ scheduleId: schedule.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            schedule.enabled ? "bg-success" : "bg-border"
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">
              {schedule.name}
            </span>
            {schedule.lastError && (
              <WarningCircleIcon
                className="size-3.5 shrink-0 text-destructive"
                aria-label="Last run failed"
              >
                <title>Last run failed</title>
              </WarningCircleIcon>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground/70">
            <span className="flex items-center gap-1">
              <ClockIcon className="size-3.5" />
              {schedule.trigger === "github_issue_opened"
                ? "GitHub issue opened"
                : describeCron(schedule.schedule!)}
            </span>
            {schedule.repo && <span>{schedule.repo}</span>}
            {schedule.slackChannelId && <span>{schedule.slackChannelId}</span>}
            <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
          </div>
          {testError && (
            <p className="mt-1 text-xs text-destructive">{testError}</p>
          )}
        </div>
      </Link>
      {schedule.lastThreadId && (
        <Link
          to="/agents/$threadId"
          params={{ threadId: schedule.lastThreadId }}
          aria-label="Open latest automation run"
          className="shrink-0 rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
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
            className="flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
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
            className="shrink-0 rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
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
