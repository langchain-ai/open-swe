import { Link } from "@tanstack/react-router"
import {
  ArrowCounterClockwiseIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  DotsSixVerticalIcon,
  KanbanIcon,
  ListBulletsIcon,
} from "@phosphor-icons/react"
import { useMemo, useState } from "react"

import type {
  AgentSource,
  AgentStatus,
  AgentThread,
} from "@/features/agents/lib/types"
import type {
  ThreadGrouping,
  ThreadViewGroup,
  ThreadsLayout,
} from "@/features/agents/lib/threadViews"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  useResolveAgentThread,
  useThreadsPage,
} from "@/features/agents/lib/queries"
import {
  THREAD_GROUPING_OPTIONS,
  groupThreadsForView,
  moveColumn,
  moveColumnBefore,
  parseColumnOrder,
  reconcileColumnOrder,
} from "@/features/agents/lib/threadViews"
import { cn, formatRelativeTime } from "@/lib/utils"

const LIST_PAGE_SIZE = 25
const BOARD_PAGE_SIZE = 100
const COLUMN_ORDER_STORAGE_PREFIX = "open-swe:thread-board-order:"

export interface ThreadsPageFilters {
  resolved?: boolean
  viewed?: boolean
  source?: AgentSource
  status?: AgentStatus
  q?: string
  page: number
  layout: ThreadsLayout
  group: ThreadGrouping
  order?: string
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
]

const STATUS_OPTIONS: Array<{ value: AgentStatus | "any"; label: string }> = [
  { value: "any", label: "Any status" },
  { value: "running", label: "Running" },
  { value: "finished", label: "Finished" },
  { value: "interrupted", label: "Interrupted" },
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

function displayStatus(status: AgentStatus): string {
  return (
    STATUS_OPTIONS.find((option) => option.value === status)?.label ?? status
  )
}

function storedColumnOrder(grouping: ThreadGrouping): string | undefined {
  if (typeof window === "undefined") return undefined
  return (
    window.localStorage.getItem(`${COLUMN_ORDER_STORAGE_PREFIX}${grouping}`) ??
    undefined
  )
}

export function AgentsThreadsPage({
  filters,
  onFiltersChange,
}: {
  filters: ThreadsPageFilters
  onFiltersChange: (next: ThreadsPageFilters) => void
}) {
  const filterQuery = filters.q ?? ""
  const [searchInput, setSearchInput] = useState({
    filterQuery,
    value: filterQuery,
  })
  const search =
    searchInput.filterQuery === filterQuery ? searchInput.value : filterQuery
  const setSearch = (value: string) => setSearchInput({ filterQuery, value })

  const orderKey = `${filters.group}\u0000${filters.order ?? ""}`
  const [orderInput, setOrderInput] = useState<{
    key: string
    order: string | undefined
  }>({ key: orderKey, order: filters.order })
  const personalOrder =
    orderInput.key === orderKey
      ? orderInput.order
      : (filters.order ?? storedColumnOrder(filters.group))
  const setPersonalOrder = (order: string | undefined) =>
    setOrderInput({ key: orderKey, order })
  const pageSize = filters.layout === "board" ? BOARD_PAGE_SIZE : LIST_PAGE_SIZE
  const offset = (filters.page - 1) * pageSize
  const query = useThreadsPage(
    {
      limit: pageSize,
      offset,
      resolved: filters.resolved,
      viewed: filters.viewed,
      source: filters.source,
      status: filters.status,
      q: filters.q,
      scope: "interactive",
    },
    { staleWhileRevalidate: true }
  )

  const data = query.data
  const items = data?.items ?? []
  const hasMore = data?.hasMore ?? false
  const exactTotal = data?.total
  const end = offset + items.length
  const groups = useMemo(
    () => groupThreadsForView(items, filters.group),
    [filters.group, items]
  )
  const defaultKeys = groups.map((group) => group.key)
  const columnOrder = reconcileColumnOrder(
    defaultKeys,
    parseColumnOrder(filters.order ?? personalOrder)
  )
  const groupsByKey = new Map(groups.map((group) => [group.key, group]))
  const orderedGroups = columnOrder
    .map((key) => groupsByKey.get(key))
    .filter((group): group is ThreadViewGroup => Boolean(group))

  const update = (patch: Partial<ThreadsPageFilters>) => {
    onFiltersChange({ ...filters, ...patch, page: patch.page ?? 1 })
  }

  const onSearchSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    update({ q: search.trim() || undefined })
  }

  const setGrouping = (group: ThreadGrouping) => {
    update({ group, order: storedColumnOrder(group) })
  }

  const setColumnOrder = (next: Array<string>) => {
    const value = next.join("|")
    setPersonalOrder(value)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        `${COLUMN_ORDER_STORAGE_PREFIX}${filters.group}`,
        value
      )
    }
    onFiltersChange({ ...filters, order: value })
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden px-6 py-8 max-md:px-4 max-md:pt-16">
      <div className="flex min-h-0 w-full flex-1 flex-col gap-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-heading text-base font-medium text-foreground">
              Threads
            </h1>
            <p className="text-xs text-muted-foreground">
              Organize interactive agent work by what needs your attention.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-md border border-border bg-card p-0.5">
              <button
                type="button"
                onClick={() => update({ layout: "board" })}
                className={cn(
                  "flex h-7 items-center gap-1.5 rounded px-2 text-xs transition-colors",
                  filters.layout === "board"
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <KanbanIcon className="size-3.5" />
                Board
              </button>
              <button
                type="button"
                onClick={() => update({ layout: "list" })}
                className={cn(
                  "flex h-7 items-center gap-1.5 rounded px-2 text-xs transition-colors",
                  filters.layout === "list"
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <ListBulletsIcon className="size-3.5" />
                List
              </button>
            </div>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Group by
              <select
                value={filters.group}
                onChange={(event) =>
                  setGrouping(event.target.value as ThreadGrouping)
                }
                className="h-8 rounded-md border border-border bg-card px-2 text-xs text-foreground outline-none"
              >
                {THREAD_GROUPING_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>

        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-3">
          <form onSubmit={onSearchSubmit} className="flex gap-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
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

        <div className="min-h-0 flex-1">
          {query.isLoading ? (
            <EmptyMessage>Loading threads…</EmptyMessage>
          ) : items.length === 0 ? (
            <EmptyMessage>No threads match these filters.</EmptyMessage>
          ) : filters.layout === "board" ? (
            <ThreadsBoard
              groups={orderedGroups}
              order={columnOrder}
              onOrderChange={setColumnOrder}
            />
          ) : (
            <ThreadsList groups={orderedGroups} />
          )}
        </div>

        {(items.length > 0 || filters.page > 1) && (
          <div className="flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
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

function ThreadsBoard({
  groups,
  order,
  onOrderChange,
}: {
  groups: Array<ThreadViewGroup>
  order: Array<string>
  onOrderChange: (order: Array<string>) => void
}) {
  const [draggedKey, setDraggedKey] = useState<string | null>(null)

  return (
    <div className="flex h-full min-h-0 gap-3 overflow-x-auto pb-3">
      {groups.map((group, index) => (
        <section
          key={group.key}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            const source =
              draggedKey || event.dataTransfer.getData("text/plain")
            if (source)
              onOrderChange(moveColumnBefore(order, source, group.key))
            setDraggedKey(null)
          }}
          className="flex min-h-0 w-[min(19rem,82vw)] shrink-0 flex-col rounded-xl border border-border bg-muted/35"
        >
          <div
            draggable
            onDragStart={(event) => {
              setDraggedKey(group.key)
              event.dataTransfer.effectAllowed = "move"
              event.dataTransfer.setData("text/plain", group.key)
            }}
            onDragEnd={() => setDraggedKey(null)}
            className={cn(
              "flex cursor-grab items-center gap-2 border-b border-border px-3 py-2.5 active:cursor-grabbing",
              draggedKey === group.key && "opacity-50"
            )}
          >
            <DotsSixVerticalIcon className="size-4 text-muted-foreground/60" />
            <h2 className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">
              {group.label}
            </h2>
            <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {group.threads.length}
            </span>
            <button
              type="button"
              disabled={index === 0}
              onClick={() => onOrderChange(moveColumn(order, group.key, -1))}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-25"
              aria-label={`Move ${group.label} left`}
            >
              <ArrowLeftIcon className="size-3" />
            </button>
            <button
              type="button"
              disabled={index === groups.length - 1}
              onClick={() => onOrderChange(moveColumn(order, group.key, 1))}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-25"
              aria-label={`Move ${group.label} right`}
            >
              <ArrowRightIcon className="size-3" />
            </button>
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
            {group.threads.map((thread) => (
              <ThreadCard key={thread.id} thread={thread} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function ThreadsList({ groups }: { groups: Array<ThreadViewGroup> }) {
  return (
    <div className="h-full overflow-y-auto">
      {groups.map((group) => (
        <section key={group.key} className="mb-5">
          <div className="mb-1 flex items-center gap-2 px-2 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            <span>{group.label}</span>
            <span>{group.threads.length}</span>
          </div>
          <div className="space-y-1">
            {group.threads.map((thread) => (
              <ThreadListItem key={thread.id} thread={thread} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function ThreadCard({ thread }: { thread: AgentThread }) {
  const resolveThread = useResolveAgentThread()
  const isResolved = thread.resolved === true
  const isResolving =
    resolveThread.isPending && resolveThread.variables.threadId === thread.id

  return (
    <article className="group rounded-lg border border-border bg-card shadow-sm transition hover:border-muted-foreground/50 hover:shadow-md">
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className="block p-3"
      >
        <div className="flex items-start gap-2">
          <StatusMark thread={thread} />
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-sm font-medium text-foreground">
              {thread.title}
            </p>
            <p className="mt-1 truncate text-[11px] text-muted-foreground/70">
              {thread.repoFullName || "No repository"}
            </p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          <span className="rounded bg-muted px-1.5 py-0.5">
            {displayStatus(thread.status)}
          </span>
          <span className="rounded bg-muted px-1.5 py-0.5 capitalize">
            {thread.source ?? "dashboard"}
          </span>
          {thread.pr && (
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">
              PR #{thread.pr.number} · {thread.pr.state}
            </span>
          )}
          {thread.diffStats && (
            <span className="rounded bg-success/10 px-1.5 py-0.5 text-success-foreground">
              +{thread.diffStats.additions} −{thread.diffStats.deletions}
            </span>
          )}
          {isResolved && (
            <span className="rounded bg-accent px-1.5 py-0.5 text-foreground">
              Resolved
            </span>
          )}
        </div>
      </Link>
      <div className="flex items-center justify-between border-t border-border/70 px-3 py-1.5">
        <span className="text-[10px] text-muted-foreground/70">
          {formatRelativeTime(thread.updatedAt)}
        </span>
        <button
          type="button"
          aria-label={isResolved ? "Reopen thread" : "Resolve thread"}
          title={isResolved ? "Reopen thread" : "Resolve thread"}
          disabled={isResolving}
          onClick={() =>
            resolveThread.mutate({ threadId: thread.id, resolved: !isResolved })
          }
          className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
        >
          {isResolved ? (
            <ArrowCounterClockwiseIcon className="size-3" />
          ) : (
            <CheckCircleIcon className="size-3" />
          )}
          {isResolved ? "Reopen" : "Resolve"}
        </button>
      </div>
    </article>
  )
}

function StatusMark({ thread }: { thread: AgentThread }) {
  if (thread.status === "running") {
    return (
      <CircleNotchIcon
        className="mt-0.5 size-3.5 shrink-0 animate-spin text-primary"
        aria-label="Running"
      />
    )
  }
  return (
    <span
      className={cn(
        "mt-1 size-2.5 shrink-0 rounded-full",
        thread.status === "error" || thread.status === "interrupted"
          ? "bg-destructive"
          : thread.status === "finished" && !thread.viewed
            ? "bg-warning"
            : "bg-border"
      )}
      aria-label={displayStatus(thread.status)}
    />
  )
}

function ThreadListItem({ thread }: { thread: AgentThread }) {
  const resolveThread = useResolveAgentThread()
  const isResolved = thread.resolved === true

  return (
    <div className="group flex items-center gap-2 rounded-lg border border-transparent px-3 py-2 transition-colors hover:border-border hover:bg-sidebar-row-hover">
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className="min-w-0 flex-1"
      >
        <p className="truncate text-sm text-foreground">{thread.title}</p>
        <p className="truncate text-[11px] text-muted-foreground/70">
          {thread.repoFullName || "No repo"} · {displayStatus(thread.status)}
          {isResolved ? " · Resolved" : ""} ·{" "}
          {formatRelativeTime(thread.updatedAt)}
        </p>
      </Link>
      <button
        type="button"
        aria-label={isResolved ? "Reopen thread" : "Resolve thread"}
        title={isResolved ? "Reopen thread" : "Resolve thread"}
        onClick={() =>
          resolveThread.mutate({ threadId: thread.id, resolved: !isResolved })
        }
        disabled={resolveThread.isPending}
        className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground/70 hover:bg-accent hover:text-foreground"
      >
        {isResolved ? (
          <ArrowCounterClockwiseIcon className="size-4" />
        ) : (
          <CheckCircleIcon className="size-4" />
        )}
      </button>
    </div>
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
      <span className="text-muted-foreground/70">{label}</span>
      <div className="flex overflow-hidden rounded-md border border-border">
        {TRI_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={cn(
              "px-2 py-0.5 transition-colors",
              value === option.value
                ? "bg-accent text-foreground"
                : "text-muted-foreground hover:bg-sidebar-row-hover"
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
      onChange={(event) => onChange(event.target.value)}
      className="h-7 rounded-md border border-border bg-card px-2 text-xs text-foreground outline-none"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

function EmptyMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border text-xs text-muted-foreground">
      {children}
    </div>
  )
}
