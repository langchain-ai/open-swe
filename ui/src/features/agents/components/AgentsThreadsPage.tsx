import { Link } from "@tanstack/react-router"
import {
  ArrowCounterClockwiseIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CheckCircleIcon,
} from "@phosphor-icons/react"
import { useState } from "react"

import type { AgentSource, AgentStatus, AgentThread } from "@/features/agents/lib/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useResolveAgentThread, useThreadsPage } from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 25

export interface ThreadsPageFilters {
  resolved?: boolean
  viewed?: boolean
  source?: AgentSource
  status?: AgentStatus
  q?: string
  page: number
}

type TriState = "any" | "true" | "false"

const TRI_OPTIONS: Array<{ value: TriState; label: string }> = [
  { value: "any", label: "Any" },
  { value: "true", label: "Yes" },
  { value: "false", label: "No" },
]

const SOURCE_OPTIONS: Array<{ value: AgentSource | "any"; label: string }> = [
  { value: "any", label: "Any source" },
  { value: "dashboard", label: "Dashboard" },
  { value: "github", label: "GitHub" },
  { value: "slack", label: "Slack" },
  { value: "linear", label: "Linear" },
  { value: "schedule", label: "Schedule" },
]

const STATUS_OPTIONS: Array<{ value: AgentStatus | "any"; label: string }> = [
  { value: "any", label: "Any status" },
  { value: "running", label: "Running" },
  { value: "finished", label: "Finished" },
  { value: "error", label: "Error" },
  { value: "idle", label: "Idle" },
]

function boolToTri(value?: boolean): TriState {
  if (value === true) return "true"
  if (value === false) return "false"
  return "any"
}

function triToBool(value: TriState): boolean | undefined {
  if (value === "true") return true
  if (value === "false") return false
  return undefined
}

export function AgentsThreadsPage({
  filters,
  onFiltersChange,
}: {
  filters: ThreadsPageFilters
  onFiltersChange: (next: ThreadsPageFilters) => void
}) {
  const [search, setSearch] = useState(filters.q ?? "")
  const offset = (filters.page - 1) * PAGE_SIZE
  const query = useThreadsPage({
    limit: PAGE_SIZE,
    offset,
    resolved: filters.resolved,
    viewed: filters.viewed,
    source: filters.source,
    status: filters.status,
    q: filters.q,
  })

  const data = query.data
  const items = data?.items ?? []
  const hasMore = data?.hasMore ?? false
  const exactTotal = data?.total
  const end = offset + items.length

  const update = (patch: Partial<ThreadsPageFilters>) => {
    onFiltersChange({ ...filters, ...patch, page: patch.page ?? 1 })
  }

  const onSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    update({ q: search.trim() || undefined })
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-y-auto px-6 py-8">
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6">
        <div>
          <h1 className="font-heading text-base font-medium text-[var(--ui-text)]">
            Threads
          </h1>
          <p className="text-xs text-[var(--ui-text-muted)]">
            Search and filter every thread, including resolved ones.
          </p>
        </div>

        <div className="flex flex-col gap-3 rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] p-3">
          <form onSubmit={onSearchSubmit} className="flex gap-2">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title..."
              className="h-8"
            />
            <Button type="submit" size="sm" variant="outline">
              Search
            </Button>
          </form>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            <TriFilter
              label="Resolved"
              value={boolToTri(filters.resolved)}
              onChange={(value) => update({ resolved: triToBool(value) })}
            />
            <TriFilter
              label="Viewed"
              value={boolToTri(filters.viewed)}
              onChange={(value) => update({ viewed: triToBool(value) })}
            />
            <SelectFilter
              value={filters.source ?? "any"}
              options={SOURCE_OPTIONS}
              onChange={(value) =>
                update({
                  source: value === "any" ? undefined : (value as AgentSource),
                })
              }
            />
            <SelectFilter
              value={filters.status ?? "any"}
              options={STATUS_OPTIONS}
              onChange={(value) =>
                update({
                  status: value === "any" ? undefined : (value as AgentStatus),
                })
              }
            />
          </div>
        </div>

        <div className="flex flex-col gap-1">
          {query.isLoading ? (
            <p className="px-2 py-8 text-center text-xs text-[var(--ui-text-muted)]">
              Loading...
            </p>
          ) : items.length === 0 ? (
            <p className="px-2 py-8 text-center text-xs text-[var(--ui-text-muted)]">
              No threads match these filters.
            </p>
          ) : (
            items.map((thread) => (
              <ThreadListItem key={thread.id} thread={thread} />
            ))
          )}
        </div>

        {(items.length > 0 || filters.page > 1) && (
          <div className="mt-auto flex items-center justify-between pt-2 text-xs text-[var(--ui-text-muted)]">
            <span>
              {items.length > 0 ? `${offset + 1}–${end}` : "No results"}
              {exactTotal != null ? ` of ${exactTotal}` : hasMore ? "+" : ""}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={filters.page <= 1}
                onClick={() => update({ page: filters.page - 1 })}
              >
                <CaretLeftIcon className="size-3" />
                Prev
              </Button>
              <span>Page {filters.page}</span>
              <Button
                size="sm"
                variant="outline"
                disabled={!hasMore}
                onClick={() => update({ page: filters.page + 1 })}
              >
                Next
                <CaretRightIcon className="size-3" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

function TriFilter({
  label,
  value,
  onChange,
}: {
  label: string
  value: TriState
  onChange: (value: TriState) => void
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[var(--ui-text-dim)]">{label}</span>
      <div className="flex overflow-hidden rounded-md border border-[var(--ui-border)]">
        {TRI_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={cn(
              "px-2 py-0.5 transition-colors",
              value === option.value
                ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
                : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]"
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function SelectFilter({
  value,
  options,
  onChange,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-7 rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel)] px-2 text-xs text-[var(--ui-text)] outline-none"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

function ThreadListItem({ thread }: { thread: AgentThread }) {
  const resolveThread = useResolveAgentThread()
  const isResolved = thread.resolved === true

  const onToggleResolved = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (resolveThread.isPending) return
    resolveThread.mutate({ threadId: thread.id, resolved: !isResolved })
  }

  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      className="group flex items-center gap-3 rounded-lg border border-transparent px-3 py-2 transition-colors hover:border-[var(--ui-border)] hover:bg-[var(--ui-sidebar-hover)]"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-[var(--ui-text)]">{thread.title}</p>
        <p className="truncate text-[11px] text-[var(--ui-text-dim)]">
          {thread.repoFullName || "no repo"} · {thread.status}
          {isResolved ? " · resolved" : ""}
        </p>
      </div>
      <button
        type="button"
        aria-label={isResolved ? "Unresolve thread" : "Resolve thread"}
        title={isResolved ? "Unresolve thread" : "Resolve thread"}
        onClick={onToggleResolved}
        disabled={resolveThread.isPending}
        className="flex size-6 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
      >
        {isResolved ? (
          <ArrowCounterClockwiseIcon className="size-4" />
        ) : (
          <CheckCircleIcon className="size-4" />
        )}
      </button>
    </Link>
  )
}
