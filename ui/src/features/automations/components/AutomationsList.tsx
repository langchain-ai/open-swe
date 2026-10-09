import { PlusIcon, StackRegularIcon } from "@langchain/macaw-components/icons"
import { Link, useNavigate } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PauseIcon } from "@phosphor-icons/react/dist/ssr/Pause"
import { PlayIcon } from "@phosphor-icons/react/dist/ssr/Play"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"

import type { AgentSchedule } from "@/features/agents/lib/types"
import { AutomationRuns } from "@/features/automations/components/AutomationRuns"
import { AutomationTemplates } from "@/features/automations/components/AutomationTemplates"
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

const TAB_OPTIONS = [
  { value: "overview", display: "Overview" },
  { value: "runs", display: "Runs" },
] as const

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
          <Banner intent="neutral" className="mt-space-3">
            Automations can also be listed and managed through Open SWE. Start a
            new thread, turn on Admin next to the model picker, then ask the
            agent to make the change.
          </Banner>
        )}
        <GroupedTabs
          className="mt-space-4"
          value={tab}
          onChange={onTabChange}
          options={TAB_OPTIONS}
        />

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
              {canManage && <NewAutomationButton />}
            </div>

            <div className="mt-3">
              {schedulesQuery.isLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full rounded-xl" />
                  <Skeleton className="h-16 w-full rounded-xl" />
                </div>
              ) : total === 0 ? (
                <EmptyState
                  className="rounded-xl border border-dashed border-default bg-surface-level-1"
                  icon={LightningIcon}
                  title="No automations yet"
                  description="Run Open SWE on a schedule or on GitHub, Slack, and Linear events — review code, triage new issues, or investigate an alert."
                  action={canManage && <NewAutomationButton />}
                />
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

function NewAutomationButton() {
  return (
    <Button
      as={<Link to="/agents/automations/new" />}
      size="md"
      leftDecorator={PlusIcon}
    >
      New Automation
    </Button>
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
    <Card className="px-space-4 py-space-3">
      <div className="text-xs text-tertiary">{label}</div>
      <div
        className={cn(
          "mt-1 text-lg font-medium",
          highlight ? "text-error-secondary" : "text-primary"
        )}
      >
        {value}
      </div>
    </Card>
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
    <div className="flex items-center gap-3 rounded-xl border border-default bg-surface-level-1 px-4 py-3 transition-colors hover:bg-surface-level-1-hover">
      <Link
        to="/agents/automations/$scheduleId"
        params={{ scheduleId: schedule.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            schedule.enabled ? "bg-success-strong" : "bg-surface-level-4"
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-primary">
              {schedule.name}
            </span>
            {schedule.lastError && (
              <WarningCircleIcon
                size={14}
                weight="regular"
                className="shrink-0 text-icon-error"
                aria-label="Last run failed"
              >
                <title>Last run failed</title>
              </WarningCircleIcon>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-tertiary">
            <span className="flex items-center gap-1">
              <ClockIcon size={14} weight="regular" />
              {describeTriggers(schedule)}
            </span>
            {workspaces.length > 1 && (
              <span className="flex items-center gap-1">
                <StackRegularIcon size={14} />
                {workspaceName}
              </span>
            )}
            <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
          </div>
        </div>
      </Link>
      {schedule.lastThreadId && (
        <IconButton
          asChild
          icon={ArrowSquareOutIcon}
          label="Open latest automation run"
          color="secondary"
          variant="plain"
          size="md"
          className="shrink-0"
        >
          <Link
            to="/agents/$threadId"
            params={{ threadId: schedule.lastThreadId }}
          />
        </IconButton>
      )}
      {canManage && (
        <>
          <Button
            color="secondary"
            variant="outlined"
            leftDecorator={LightningIcon}
            onClick={onTest}
            disabled={isTesting || isToggling}
            aria-label="Test automation"
            className="shrink-0"
          >
            {isTesting ? "Starting…" : "Test"}
          </Button>
          <IconButton
            icon={schedule.enabled ? PauseIcon : PlayIcon}
            label={schedule.enabled ? "Pause automation" : "Resume automation"}
            color="secondary"
            variant="plain"
            size="md"
            onClick={onToggle}
            disabled={isTesting || isToggling}
            className="shrink-0"
          />
        </>
      )}
    </div>
  )
}
