import { Link } from "@tanstack/react-router"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import { AlertTriangle, Clock } from "@/components/glyphs"
import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { formatRelativeTime } from "@/lib/utils"

type StatusTone = "positive" | "risk" | "info" | "neutral"

const RUN_STATUS: Record<AgentStatus, { label: string; tone: StatusTone }> = {
  running: { label: "Running", tone: "info" },
  finished: { label: "Finished", tone: "positive" },
  interrupted: { label: "Interrupted", tone: "risk" },
  error: { label: "Error", tone: "risk" },
  idle: { label: "Idle", tone: "neutral" },
}

const RUN_ROW_CLASS =
  "flex min-h-row-record w-full items-center gap-3 px-3 py-2 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"

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

  if (runsQuery.isLoading) return <RunListSkeleton />
  if (runsQuery.isError) {
    return (
      <StateNotice
        tone="RISK"
        icon={AlertTriangle}
        title="Automation runs could not be loaded."
        description="Your automations still run; only this history is missing."
        action={
          <Button
            type="button"
            size="compact"
            variant="outline"
            onClick={() => void runsQuery.refetch()}
            disabled={runsQuery.isFetching}
          >
            {runsQuery.isFetching ? "Retrying…" : "Retry"}
          </Button>
        }
      />
    )
  }
  if (runs.length === 0) {
    return (
      <EmptyState
        icon={Clock}
        title="No automation runs yet"
        description="Each run appears here with the thread it started."
      />
    )
  }

  return (
    <Stack gap="2xl">
      {automationId ? (
        <RunList runs={grouped[0]?.runs ?? []} />
      ) : (
        grouped.map((group) => (
          <PageSection
            key={group.id}
            title={group.name}
            description={`${group.runs.length} ${group.runs.length === 1 ? "run" : "runs"}`}
          >
            <RunList runs={group.runs} />
          </PageSection>
        ))
      )}
      {runsQuery.data?.hasMore && (
        <Box render={<p />} className="text-center text-meta text-ink-subtle">
          Showing the {limit} most recent runs.
        </Box>
      )}
    </Stack>
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

function RunList({ runs }: { runs: Array<AgentThread> }) {
  return (
    <Stack render={<ul />} gap="none" className="border-t border-line">
      {runs.map((run) => (
        <Box
          key={run.id}
          render={<li />}
          className="border-b border-line last:border-b-0"
        >
          <AutomationRunRow run={run} />
        </Box>
      ))}
    </Stack>
  )
}

export function RunStatusBadge({ status }: { status: AgentStatus }) {
  const { label, tone } = RUN_STATUS[status]
  return (
    <Badge tone={tone} dot={status !== "running"}>
      {status === "running" && <Spinner size="sm" />}
      {label}
    </Badge>
  )
}

function AutomationRunRow({ run }: { run: AgentThread }) {
  const isTest = run.triggerKind === "schedule_test"
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: run.id }}
      className={RUN_ROW_CLASS}
    >
      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className="truncate text-label font-medium text-ink"
        >
          {run.title}
        </Box>
        <Inline
          render={<span />}
          gap="sm"
          wrap
          className="text-meta text-ink-subtle"
        >
          <span>{isTest ? "Test run" : "Scheduled run"}</span>
          {run.automationActionPosted && (
            <Inline
              render={<span />}
              gap="xs"
              ink="positive"
              aria-label="Action posted to Slack"
            >
              <ProviderLogo
                provider="slack"
                branded={false}
                className="size-3"
              />
              Posted to Slack
            </Inline>
          )}
          {run.repoFullName && <span>{run.repoFullName}</span>}
        </Inline>
      </Stack>
      <Inline render={<span />} justify="end" className="w-27.5 shrink-0">
        <RunStatusBadge status={run.status} />
      </Inline>
      <Box
        render={<span />}
        className="w-20 shrink-0 text-right text-meta text-ink-subtle tabular-nums"
      >
        {formatRelativeTime(run.updatedAt)}
      </Box>
    </Link>
  )
}

function RunListSkeleton() {
  return (
    <Stack
      gap="none"
      aria-busy
      aria-label="Loading automation runs"
      className="border-t border-line"
    >
      {[0, 1, 2].map((row) => (
        <Inline
          key={row}
          gap="md"
          className="min-h-row-record border-b border-line px-3 py-2 last:border-b-0"
        >
          <Stack gap="xs" className="min-w-0 flex-1">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-32" />
          </Stack>
          <Skeleton className="h-5 w-20 rounded-badge" />
        </Inline>
      ))}
    </Stack>
  )
}
