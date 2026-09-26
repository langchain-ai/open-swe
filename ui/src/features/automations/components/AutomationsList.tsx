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
import { PageHeader } from "@/components/AppShell"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { describeCron } from "@/features/automations/lib/cron"
import {
  agentMutationKeys,
  useAgentSchedules,
  useTriggerAgentSchedule,
  useUpdateAgentSchedule,
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
        <PageHeader
          className="mb-0"
          description={
            <>
              Run Open SWE on a recurring schedule. Each run starts a fresh
              agent thread.{" "}
              {!canManage && "Workspace admins manage automation setup."}
            </>
          }
          title="Automations"
        />
        {canManage && (
          <p className="mt-3 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
            Automations can also be listed and managed through Open SWE. Start a
            new thread, turn on Admin next to the model picker, then ask the
            agent to make the change.
          </p>
        )}
        <Tabs
          className="mt-4"
          onValueChange={(value: AutomationsTab) => onTabChange(value)}
          value={tab}
        >
          <TabsList>
            {(["overview", "runs"] as const).map((value) => (
              <TabsTrigger
                className="px-3 capitalize"
                key={value}
                value={value}
              >
                {value}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

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
    <Empty className="border border-border bg-card py-14">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <LightningIcon />
        </EmptyMedia>
        <EmptyTitle>No automations yet</EmptyTitle>
        <EmptyDescription>
          Schedule Open SWE to run on a recurring cadence — review code, triage
          issues, or keep docs up to date.
        </EmptyDescription>
      </EmptyHeader>
      {canManage && (
        <EmptyContent>
          <Link to="/agents/automations/new" className={buttonVariants()}>
            <PlusIcon className="size-4" />
            New Automation
          </Link>
        </EmptyContent>
      )}
    </Empty>
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
                : schedule.schedule
                  ? describeCron(schedule.schedule)
                  : "No trigger"}
            </span>
            {schedule.repo && <span>{schedule.repo}</span>}
            {schedule.slackChannelId && <span>{schedule.slackChannelId}</span>}
            <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
          </div>
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
          <Button
            type="button"
            variant="outline"
            onClick={onTest}
            disabled={isTesting || isToggling}
            className="text-muted-foreground"
          >
            <LightningIcon />
            {isTesting ? "Starting…" : "Test"}
          </Button>
          <TooltipIconButton
            label={schedule.enabled ? "Pause automation" : "Resume automation"}
            onClick={onToggle}
            disabled={isTesting || isToggling}
            size="icon"
          >
            {schedule.enabled ? (
              <PauseIcon className="size-4" />
            ) : (
              <PlayIcon className="size-4" />
            )}
          </TooltipIconButton>
        </>
      )}
    </div>
  )
}
