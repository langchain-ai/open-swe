import { PlusIcon } from "@langchain/macaw-components/icons"
import { CopyErrorButton } from "@/components/CopyErrorButton"
import { Text } from "@langchain/macaw-components/Text"
import { Select } from "@langchain/macaw-components/Select"
import { Link, useNavigate } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Divider } from "@langchain/macaw-components/Divider"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { Skeleton } from "@/components/Skeleton"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PauseIcon } from "@phosphor-icons/react/dist/ssr/Pause"
import { PlayIcon } from "@phosphor-icons/react/dist/ssr/Play"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type {
  AgentSchedule,
  AutomationTriggerConfig,
} from "@/features/agents/lib/types"
import { AutomationIconTile } from "@/features/automations/components/AutomationIconTile"
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
import { cn, formatRelativeTime } from "@/lib/utils"

export type AutomationsTab = "automations" | "runs"

const TAB_OPTIONS = [
  { value: "automations", display: "Automations" },
  { value: "runs", display: "Runs" },
] as const

export type AutomationsFilter = "mine" | "system" | "all"

// Only admins see system automations and other people's, so only they get views.
const FILTER_OPTIONS = [
  { value: "mine", display: "Mine" },
  { value: "system", display: "System" },
  { value: "all", display: "All" },
] as const

// Other people's personal runs are private even to admins, so runs split only into
// the viewer's own and every run they can read, system automations' included.
const RUNS_FILTER_OPTIONS = [
  { value: "mine", display: "Mine" },
  { value: "all", display: "All" },
] as const

const EMPTY_TITLES: Record<AutomationsFilter, string> = {
  mine: "No automations yet",
  system: "No system automations",
  all: "No automations yet",
}

const TRIGGER_ICONS: Record<AutomationTriggerConfig["kind"], IconComponent> = {
  schedule: ClockIcon,
  github: GithubLogoIcon,
  slack: SlackLogoIcon,
  linear: KanbanIcon,
}

export function AutomationsList({
  tab,
  onTabChange,
  chosenFilter,
  onFilterChange,
  ownerId,
  onOwnerChange,
}: {
  tab: AutomationsTab
  onTabChange: (tab: AutomationsTab) => void
  chosenFilter: AutomationsFilter
  onFilterChange: (filter: AutomationsFilter) => void
  ownerId?: string
  onOwnerChange: (ownerId: string | undefined) => void
}) {
  const session = useSession().data
  const isAdmin = session?.is_admin === true
  // A member sees their own automations alone.
  const filter = isAdmin ? chosenFilter : "mine"
  const schedulesQuery = useAgentSchedules({
    allUsers: isAdmin && filter === "all",
  })
  const canManage = Boolean(session)
  const allSchedules = schedulesQuery.data ?? []
  const owners = new Map<string, string>()
  for (const schedule of allSchedules) {
    if (!schedule.system && schedule.ownerId) {
      owners.set(schedule.ownerId, schedule.createdBy || schedule.ownerId)
    }
  }
  const ownerOptions = [
    { value: "all", label: "All users" },
    ...Array.from(owners, ([value, label]) => ({ value, label })).sort((a, b) =>
      a.label.localeCompare(b.label)
    ),
  ]
  const schedules = allSchedules.filter((schedule) => {
    if (filter === "all") {
      return !ownerId || (!schedule.system && schedule.ownerId === ownerId)
    }
    if (filter === "system") return schedule.system === true
    return (
      !schedule.system &&
      !!session?.user_id &&
      schedule.ownerId === session.user_id
    )
  })

  const loading = schedulesQuery.isLoading
  const failed = schedulesQuery.isError && !schedulesQuery.data
  const isEmpty = !loading && !failed && schedules.length === 0
  const offerTemplates =
    !loading && canManage && filter === "mine" && tab === "automations"

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="w-full max-w-6xl px-space-5 pt-space-8 pb-space-9 max-md:pt-space-9">
        <header className="flex flex-wrap items-start justify-between gap-space-4">
          <Text variant="h1">Automations</Text>
          {canManage && (
            <Button
              as={<Link to="/agents/automations/new" />}
              leftDecorator={PlusIcon}
            >
              New automation
            </Button>
          )}
        </header>

        <div className="mt-space-5">
          <GroupedTabs
            value={tab}
            onChange={onTabChange}
            options={TAB_OPTIONS}
          />
        </div>

        {isAdmin && (
          <div className="mt-space-4 flex flex-wrap items-center gap-space-2">
            {tab === "runs" ? (
              <GroupedTabs
                value={filter === "mine" ? "mine" : "all"}
                onChange={onFilterChange}
                options={RUNS_FILTER_OPTIONS}
              />
            ) : (
              <GroupedTabs
                value={filter}
                onChange={onFilterChange}
                options={FILTER_OPTIONS}
              />
            )}
            {tab === "automations" && filter === "all" && (
              <Select
                aria-label="Filter by user"
                value={ownerId ?? "all"}
                onChange={(value) =>
                  onOwnerChange(value === "all" ? undefined : value)
                }
                options={ownerOptions}
                hideSearch={false}
                searchPlaceholder="Search users…"
                emptyText="No users found"
                placeholder="Select user"
                loading={schedulesQuery.isLoading}
                size="sm"
                triggerClassName="w-44"
              />
            )}
          </div>
        )}

        {tab === "runs" && (
          <div className="mt-space-6">
            <AutomationRuns
              key={filter === "mine" ? "mine" : "all"}
              owned={filter === "mine"}
            />
          </div>
        )}

        {tab === "automations" && (
          <>
            <div className="mt-space-4">
              {loading ? (
                <div role="status" aria-label="Loading automations">
                  <AutomationRowSkeleton />
                  <AutomationRowSkeleton />
                  <AutomationRowSkeleton />
                </div>
              ) : failed ? (
                <Banner
                  intent="error"
                  action={
                    <Button
                      color="secondary"
                      variant="outlined"
                      onClick={() => void schedulesQuery.refetch()}
                      disabled={schedulesQuery.isFetching}
                    >
                      {schedulesQuery.isFetching ? "Retrying…" : "Retry"}
                    </Button>
                  }
                >
                  Automations could not be loaded.
                </Banner>
              ) : isEmpty ? (
                <EmptyState
                  className="py-space-6"
                  icon={ClockIcon}
                  title={
                    filter === "all" && ownerId
                      ? "No automations for this user"
                      : EMPTY_TITLES[filter]
                  }
                  description={
                    filter === "all" && ownerId
                      ? "Choose another user or All users to see more automations."
                      : "Get a daily briefing, a weekly review, or a heads-up when something changes, without asking each time."
                  }
                />
              ) : (
                <ul aria-label="Automations" className="flex flex-col">
                  {schedules.map((schedule) => (
                    <AutomationRow
                      key={schedule.id}
                      schedule={schedule}
                      showOwner={filter !== "mine"}
                      canManage={
                        isAdmin ||
                        (!schedule.system &&
                          !!session?.user_id &&
                          schedule.ownerId === session.user_id)
                      }
                    />
                  ))}
                </ul>
              )}
            </div>

            {offerTemplates && (
              <>
                <Divider className="my-space-7" />
                <Text as="h2" variant="sm" weight="medium" color="secondary">
                  Start from a template
                </Text>
                <AutomationTemplates className="mt-space-3" />
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** The shape of an `AutomationRow`: icon tile, name, trigger line. */
function AutomationRowSkeleton() {
  return (
    <div className="flex items-center gap-space-3 px-space-3 py-space-3">
      <Skeleton className="size-9 shrink-0 rounded-lg" />
      <div className="min-w-0 flex-1 space-y-space-2">
        <Skeleton className="h-3.5 w-48 max-w-full" />
        <Skeleton className="h-3 w-72 max-w-full" />
      </div>
    </div>
  )
}

function lastRunLabel(value?: string | null): string {
  const time = value ? new Date(value).getTime() : Number.NaN
  return Number.isNaN(time)
    ? "Never run"
    : `Last run ${formatRelativeTime(time)}`
}

function AutomationRow({
  schedule,
  showOwner,
  canManage,
}: {
  schedule: AgentSchedule
  showOwner: boolean
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
  const busy = isStarting || isToggling
  const firstTrigger = schedule.triggers[0]

  const onRun = () => {
    if (busy) return
    triggerSchedule.mutate(schedule.id, {
      onSuccess: (result) => {
        void navigate({
          to: "/agents/$threadId",
          params: { threadId: result.thread_id },
        })
      },
    })
  }

  const onToggle = () => {
    if (busy) return
    updateSchedule.mutate({
      scheduleId: schedule.id,
      body: { enabled: !schedule.enabled },
    })
  }

  const creator = schedule.createdBy
    ? schedule.system
      ? `System · created by ${schedule.createdBy}`
      : `By ${schedule.createdBy}`
    : schedule.system
      ? "System"
      : null
  const editor =
    schedule.updatedBy && schedule.updatedBy !== schedule.createdBy
      ? `edited by ${schedule.updatedBy}`
      : null
  const owner = [creator, editor].filter(Boolean).join(" · ")

  return (
    <li className="group flex items-start gap-space-3 rounded-lg px-space-3 py-space-3 transition-colors hover:bg-surface-level-1-hover">
      <div className="min-w-0 flex-1">
        <Link
          to="/agents/automations/$scheduleId"
          params={{ scheduleId: schedule.id }}
          className="flex min-w-0 items-start gap-space-3"
        >
          <AutomationIconTile
            icon={
              firstTrigger ? TRIGGER_ICONS[firstTrigger.kind] : LightningIcon
            }
          />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-space-2">
              <span className="truncate text-sm font-medium text-primary">
                {schedule.name}
              </span>
              {!schedule.enabled && <StatusBadge>Paused</StatusBadge>}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-space-2 text-xs text-tertiary">
              <span className="break-all">{describeTriggers(schedule)}</span>
              <span aria-hidden>·</span>
              <span>{lastRunLabel(schedule.lastTriggeredAt)}</span>
              {showOwner && owner && (
                <>
                  <span aria-hidden>·</span>
                  <span className="break-all">{owner}</span>
                </>
              )}
            </div>
          </div>
        </Link>
        {schedule.lastError && (
          <div className="mt-space-1 flex min-w-0 items-center gap-space-1 pl-space-8">
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
        )}
      </div>
      <div
        className={cn(
          "flex shrink-0 items-center gap-0.5 self-center transition-opacity",
          // Revealed on hover where there is one; a touch screen always shows them.
          "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
        )}
      >
        {schedule.lastThreadId && (
          <IconButton
            asChild
            icon={ArrowSquareOutIcon}
            label="Open latest run"
            color="secondary"
            variant="plain"
            size="md"
          >
            <Link
              to="/agents/$threadId"
              params={{ threadId: schedule.lastThreadId }}
            />
          </IconButton>
        )}
        {canManage && (
          <>
            <IconButton
              icon={LightningIcon}
              label={isStarting ? "Starting…" : "Run now"}
              color="secondary"
              variant="plain"
              size="md"
              onClick={onRun}
              disabled={busy}
              className="disabled:bg-transparent"
            />
            <IconButton
              icon={schedule.enabled ? PauseIcon : PlayIcon}
              label={schedule.enabled ? "Pause" : "Resume"}
              color="secondary"
              variant="plain"
              size="md"
              onClick={onToggle}
              disabled={busy}
              className="disabled:bg-transparent"
            />
          </>
        )}
      </div>
    </li>
  )
}
