import { PlusIcon } from "@langchain/macaw-components/icons"
import { CopyErrorButton } from "@/components/CopyErrorButton"
import { Text } from "@langchain/macaw-components/Text"
import { useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@/components/Skeleton"
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

export type AutomationsFilter = "mine" | "system" | "all"

// Only admins see system automations and other people's, so only they get views.
const FILTER_OPTIONS = [
  { value: "mine", display: "Mine" },
  { value: "system", display: "System" },
  { value: "all", display: "All" },
] as const

const EMPTY_TITLES: Record<AutomationsFilter, string> = {
  mine: "No automations yet",
  system: "No system automations",
  all: "No automations yet",
}

export function AutomationsList({
  tab,
  onTabChange,
}: {
  tab: AutomationsTab
  onTabChange: (tab: AutomationsTab) => void
}) {
  const session = useSession().data
  const isAdmin = session?.is_admin === true
  const [chosenFilter, setFilter] = useState<AutomationsFilter>("mine")
  // A member sees their own automations alone.
  const filter = isAdmin ? chosenFilter : "mine"
  const schedulesQuery = useAgentSchedules({
    allUsers: isAdmin && filter === "all",
  })
  const canManage = Boolean(session)
  const schedules = (schedulesQuery.data ?? []).filter((schedule) => {
    if (filter === "all") return true
    if (filter === "system") return schedule.system === true
    return (
      !schedule.system &&
      !!session?.user_id &&
      schedule.ownerId === session.user_id
    )
  })

  const loading = schedulesQuery.isLoading
  const total = schedules.length
  const active = schedules.filter((schedule) => schedule.enabled).length
  const paused = total - active
  const issues = schedules.filter((schedule) => !!schedule.lastError).length

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-space-5 py-space-6 max-md:pt-space-9">
        <Text as="h1" variant="h3" weight="medium" color="primary">
          Automations
        </Text>
        <p className="mt-space-1 text-xs text-secondary">
          Run Robin on a schedule or on GitHub, Slack, and Linear events. Each
          run starts a fresh agent thread. Your automations run as you, on Auto.
        </p>
        {canManage && (
          <Banner intent="neutral" className="mt-space-3">
            Automations can also be listed and managed through Robin. Start a
            private thread and ask the agent to make the change.
          </Banner>
        )}
        <GroupedTabs
          className="mt-space-4"
          value={tab}
          onChange={onTabChange}
          options={TAB_OPTIONS}
        />

        {tab === "runs" ? (
          <div className="mt-space-5">
            <AutomationRuns />
          </div>
        ) : (
          <>
            <div className="mt-space-5 grid grid-cols-2 gap-space-3 sm:grid-cols-4">
              <StatCard label="Total" value={total} loading={loading} />
              <StatCard label="Active" value={active} loading={loading} />
              <StatCard label="Paused" value={paused} loading={loading} />
              <StatCard
                label="Needs attention"
                value={issues}
                loading={loading}
                highlight={issues > 0}
              />
            </div>

            <div className="mt-space-6 flex min-h-8 items-center justify-between">
              <div className="flex items-center gap-space-3">
                {loading ? (
                  <Skeleton className="h-3 w-20" />
                ) : (
                  <span className="text-xs font-medium text-secondary">
                    {total} {total === 1 ? "automation" : "automations"}
                  </span>
                )}
                {isAdmin && (
                  <GroupedTabs
                    value={filter}
                    onChange={setFilter}
                    options={FILTER_OPTIONS}
                  />
                )}
              </div>
              {/* The empty state carries its own button, so this one is for adding more. */}
              {canManage && !loading && total > 0 && <NewAutomationButton />}
            </div>

            <div className="mt-space-3">
              {loading ? (
                <div
                  role="status"
                  aria-label="Loading automations"
                  className="space-y-space-2"
                >
                  <AutomationRowSkeleton />
                  <AutomationRowSkeleton />
                  <AutomationRowSkeleton />
                </div>
              ) : total === 0 ? (
                <EmptyState
                  className="rounded-xl border border-dashed border-default bg-surface-level-1"
                  icon={LightningIcon}
                  title={EMPTY_TITLES[filter]}
                  description="Run Robin on a schedule or on GitHub, Slack, and Linear events — review code, triage new issues, or investigate an alert."
                  action={canManage && <NewAutomationButton />}
                />
              ) : (
                <div className="space-y-space-2">
                  {schedules.map((schedule) => (
                    <AutomationRow
                      key={schedule.id}
                      schedule={schedule}
                      canManage={
                        isAdmin ||
                        (!schedule.system &&
                          !!session?.user_id &&
                          schedule.ownerId === session.user_id)
                      }
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
    <Button as={<Link to="/agents/automations/new" />} leftDecorator={PlusIcon}>
      New automation
    </Button>
  )
}

function StatCard({
  label,
  value,
  loading,
  highlight,
}: {
  label: string
  value: number
  loading: boolean
  highlight?: boolean
}) {
  return (
    <Card className="px-space-4 py-space-3">
      <div className="text-xs text-tertiary">{label}</div>
      {loading ? (
        <div className="mt-space-1 flex h-7 items-center">
          <Skeleton className="h-5 w-8" />
        </div>
      ) : (
        <div
          className={cn(
            "mt-space-1 text-lg font-medium",
            highlight ? "text-error-secondary" : "text-primary"
          )}
        >
          {value}
        </div>
      )}
    </Card>
  )
}

/** The shape of an `AutomationRow`: status dot, name, trigger line, actions. */
function AutomationRowSkeleton() {
  return (
    <div className="flex items-center gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3">
      <Skeleton className="size-2 shrink-0 rounded-full" />
      <div className="min-w-0 flex-1 space-y-space-2 py-space-1">
        <Skeleton className="h-3.5 w-48 max-w-full" />
        <Skeleton className="h-3 w-72 max-w-full" />
        <Skeleton className="h-3 w-56 max-w-full" />
      </div>
      <Skeleton className="h-8 w-24 shrink-0 rounded-md" />
      <Skeleton className="size-8 shrink-0 rounded-md" />
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
  const isToggling = usePendingVariables<{ scheduleId: string }>(
    agentMutationKeys.updateSchedule
  ).some((vars) => vars.scheduleId === schedule.id)
  const isStarting =
    triggerSchedule.isPending && triggerSchedule.variables === schedule.id

  const onRun = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isStarting || isToggling) return
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
    if (isStarting || isToggling) return
    updateSchedule.mutate({
      scheduleId: schedule.id,
      body: { enabled: !schedule.enabled },
    })
  }

  return (
    <div className="flex items-center gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3 transition-colors hover:bg-surface-level-1-hover">
      <div className="min-w-0 flex-1">
        <Link
          to="/agents/automations/$scheduleId"
          params={{ scheduleId: schedule.id }}
          className="flex min-w-0 items-center gap-space-3"
        >
          <span
            className={cn(
              "size-2 shrink-0 rounded-full",
              schedule.enabled ? "bg-success-strong" : "bg-surface-level-4"
            )}
          />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-space-2">
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
            <div className="mt-space-1 flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-xs text-tertiary">
              <span className="flex items-center gap-space-1">
                <ClockIcon size={14} weight="regular" />
                {describeTriggers(schedule)}
              </span>
              <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
            </div>
            <div className="mt-space-1 flex flex-wrap gap-x-space-3 gap-y-space-1 text-xs break-all text-tertiary">
              <span>
                {schedule.system ? "System · created by" : "Created by"}:{" "}
                {schedule.createdBy || "Unknown"}
              </span>
              <span>
                Last edited by:{" "}
                {schedule.updatedBy || schedule.createdBy || "Unknown"}
              </span>
            </div>
          </div>
        </Link>
        {schedule.lastError && (
          <div className="mt-space-1 flex min-w-0 items-center gap-space-3">
            <span aria-hidden className="size-2 shrink-0" />
            <div className="flex min-w-0 items-center gap-space-1">
              <Link
                to="/agents/automations/$scheduleId"
                params={{ scheduleId: schedule.id }}
                className="truncate text-xs text-error-secondary"
              >
                Last run failed: {schedule.lastError.trim().split("\n", 1)[0]}
              </Link>
              <CopyErrorButton
                error={schedule.lastError}
                details={{
                  Automation: `${schedule.name} (${schedule.id})`,
                  "Failed at": schedule.lastErrorAt,
                  "Last thread": schedule.lastThreadId,
                }}
                className="shrink-0"
              />
            </div>
          </div>
        )}
      </div>
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
            onClick={onRun}
            disabled={isStarting || isToggling}
            aria-label="Run automation now"
            className="shrink-0"
          >
            {isStarting ? "Starting…" : "Run now"}
          </Button>
          <IconButton
            icon={schedule.enabled ? PauseIcon : PlayIcon}
            label={schedule.enabled ? "Pause automation" : "Resume automation"}
            color="secondary"
            variant="plain"
            size="md"
            onClick={onToggle}
            disabled={isStarting || isToggling}
            className="shrink-0"
          />
        </>
      )}
    </div>
  )
}
