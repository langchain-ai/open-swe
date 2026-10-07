import { Link, useNavigate } from "@tanstack/react-router"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageMasthead } from "@langchain/gtm-platform-design-system/patterns/page-masthead"
import type { PageMastheadCount } from "@langchain/gtm-platform-design-system/patterns/page-masthead"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import {
  AlertTriangle,
  Clock,
  Ellipsis,
  ExternalLink,
  Info,
  Layers,
  Pause,
  Play,
  Plus,
  Zap,
} from "@/components/glyphs"
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

const TABS: Array<{ value: AutomationsTab; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "runs", label: "Runs" },
]

const ROW_LINK_CLASS =
  "flex min-h-row-record min-w-0 flex-1 items-center gap-3 py-2 pl-3 outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"

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
  const counts: Array<PageMastheadCount> = schedulesQuery.data
    ? [
        {
          id: "total",
          label: `${total} ${total === 1 ? "automation" : "automations"}`,
        },
        { id: "active", label: `${active} active` },
        { id: "paused", label: `${paused} paused` },
        ...(issues > 0
          ? [
              {
                id: "attention",
                label: `${issues} ${issues === 1 ? "needs" : "need"} attention`,
                tone: "attention" as const,
                icon: AlertTriangle,
              },
            ]
          : []),
      ]
    : []

  return (
    <ScrollArea overflow="vertical" className="h-full min-h-0 min-w-0 flex-1">
      <Stack
        gap="xl"
        className="mx-auto w-full max-w-work px-6 py-6 max-md:pt-16"
      >
        <PageMasthead
          title="Automations"
          description={`Run Open SWE on a schedule or on GitHub, Slack, and Linear events. Each run starts a fresh agent thread.${canManage ? "" : " Workspace admins manage automation setup."}`}
          counts={counts}
        />
        {canManage && (
          <Alert tone="neutral" icon={Info}>
            <AlertDescription>
              Automations can also be listed and managed through Open SWE. Start
              a new thread, turn on Admin next to the model picker, then ask the
              agent to make the change.
            </AlertDescription>
          </Alert>
        )}
        <Inline gap="md" justify="between" wrap>
          <ToggleGroup
            aria-label="Automations view"
            value={[tab]}
            onValueChange={(value) => {
              const next = value[0]
              if (next) onTabChange(next)
            }}
          >
            {TABS.map((item) => (
              <ToggleGroupItem key={item.value} value={item.value}>
                {item.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          {canManage && <NewAutomationLink />}
        </Inline>

        {tab === "runs" ? (
          <AutomationRuns />
        ) : (
          <>
            {schedulesQuery.isLoading ? (
              <ListSkeleton />
            ) : schedulesQuery.isError ? (
              <StateNotice
                tone="RISK"
                icon={AlertTriangle}
                title="Automations could not be loaded"
                description={schedulesQuery.error.message}
                action={
                  <Button
                    type="button"
                    size="compact"
                    variant="outline"
                    disabled={schedulesQuery.isFetching}
                    onClick={() => void schedulesQuery.refetch()}
                  >
                    Try again
                  </Button>
                }
              />
            ) : total === 0 ? (
              <EmptyState
                icon={Zap}
                title="No automations yet"
                description="Review code, triage new issues, or investigate an alert on a schedule or an event."
                action={canManage ? <NewAutomationLink /> : undefined}
              />
            ) : (
              <Stack
                render={<ul aria-label="Automations" />}
                gap="none"
                bg="panel"
                border="line"
                radius="panel"
                className="overflow-hidden"
              >
                {schedules.map((schedule) => (
                  <AutomationRow
                    key={schedule.id}
                    schedule={schedule}
                    canManage={canManage}
                  />
                ))}
              </Stack>
            )}

            {canManage && <AutomationTemplates />}
          </>
        )}
      </Stack>
    </ScrollArea>
  )
}

function NewAutomationLink() {
  return (
    <Link to="/agents/automations/new" className={buttonVariants()}>
      <Icon icon={Plus} size="sm" />
      New Automation
    </Link>
  )
}

function ListSkeleton() {
  return (
    <Stack
      gap="none"
      bg="panel"
      border="line"
      radius="panel"
      aria-busy
      aria-label="Loading automations"
      className="overflow-hidden"
    >
      {[0, 1].map((row) => (
        <Inline
          key={row}
          gap="md"
          className="min-h-row-record border-b border-line px-3 py-2 last:border-b-0"
        >
          <Stack gap="xs" className="min-w-0 flex-1">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-72" />
          </Stack>
          <Skeleton className="h-5 w-16 rounded-badge" />
        </Inline>
      ))}
    </Stack>
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
  const busy = isTesting || isToggling

  const onTest = () => {
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

  return (
    <Inline
      render={<li />}
      gap="sm"
      className="border-b border-line pr-3 last:border-b-0 hover:bg-hover"
    >
      <Link
        to="/agents/automations/$scheduleId"
        params={{ scheduleId: schedule.id }}
        className={ROW_LINK_CLASS}
      >
        <Stack gap="none" className="min-w-0 flex-1">
          <Inline gap="sm" className="min-w-0">
            <Box
              render={<span />}
              className="truncate text-label font-medium text-ink"
            >
              {schedule.name}
            </Box>
            {schedule.lastError && (
              <Icon
                icon={AlertTriangle}
                size="sm"
                label="Last run failed"
                className="text-risk"
              />
            )}
          </Inline>
          <Inline
            render={<span />}
            gap="md"
            wrap
            className="text-meta text-ink-subtle"
          >
            <Inline render={<span />} gap="xs">
              <Icon icon={Clock} size="sm" />
              {describeTriggers(schedule)}
            </Inline>
            {workspaces.length > 1 && (
              <Inline render={<span />} gap="xs">
                <Icon icon={Layers} size="sm" />
                {workspaceName}
              </Inline>
            )}
            <span>Last run: {formatDate(schedule.lastTriggeredAt)}</span>
          </Inline>
        </Stack>
        <Inline render={<span />} justify="end" className="w-27.5 shrink-0">
          {isTesting ? (
            <Badge tone="info">
              <Spinner size="sm" />
              Starting…
            </Badge>
          ) : (
            <Badge tone={schedule.enabled ? "positive" : "neutral"} dot>
              {schedule.enabled ? "Active" : "Paused"}
            </Badge>
          )}
        </Inline>
      </Link>
      <Inline gap="xs" justify="end" className="w-15 shrink-0">
        {schedule.lastThreadId && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: schedule.lastThreadId }}
            aria-label="Open latest automation run"
            className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
          >
            <Icon icon={ExternalLink} size="sm" />
          </Link>
        )}
        {canManage && (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Actions for ${schedule.name}`}
                />
              }
            >
              <Icon icon={Ellipsis} size="sm" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              <DropdownMenuItem disabled={busy} onClick={onTest}>
                <Icon icon={Zap} size="sm" />
                Test automation
              </DropdownMenuItem>
              <DropdownMenuItem disabled={busy} onClick={onToggle}>
                <Icon icon={schedule.enabled ? Pause : Play} size="sm" />
                {schedule.enabled ? "Pause automation" : "Resume automation"}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </Inline>
    </Inline>
  )
}
