import type { AgentSource } from "./types"

export interface SidebarFilters {
  sources: Array<AgentSource>
  includeAutomations: boolean
  includeResolved: boolean
  ownedOnly: boolean
  includeSlack: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  sources: [],
  includeAutomations: false,
  includeResolved: false,
  ownedOnly: true,
  includeSlack: true,
}

interface FilterableThread {
  id?: string
  source?: AgentSource
  ownerId?: string | null
  threadCategory?: string
  resolved?: boolean
  taskWorkers?: Array<FilterableThread>
}

function threadSource(thread: FilterableThread): AgentSource {
  return thread.source ?? "dashboard"
}

function isAutomationThread(thread: FilterableThread): boolean {
  return (
    thread.threadCategory === "automation" ||
    threadSource(thread) === "schedule"
  )
}

/** Apply the active filter dimensions to a list of threads. */
export function filterThreads<T extends FilterableThread>(
  threads: Array<T>,
  filters: SidebarFilters,
  activeThreadId?: string,
  viewerId?: string | null
): Array<T> {
  const visible = (thread: FilterableThread) =>
    (filters.includeResolved || !thread.resolved) &&
    (filters.includeSlack || threadSource(thread) !== "slack") &&
    (filters.includeAutomations ||
      !isAutomationThread(thread) ||
      filters.sources.includes("schedule")) &&
    (!filters.ownedOnly || (Boolean(viewerId) && thread.ownerId === viewerId))
  const matchesSource = (thread: FilterableThread) =>
    (Boolean(activeThreadId) && thread.id === activeThreadId) ||
    filters.sources.length === 0 ||
    filters.sources.includes(threadSource(thread))
  return threads.flatMap((thread) => {
    if (!visible(thread)) return []
    const matches = matchesSource(thread)
    const taskWorkers = thread.taskWorkers?.filter(
      (worker) => visible(worker) && (matches || matchesSource(worker))
    )
    return matches || taskWorkers?.length
      ? [taskWorkers ? { ...thread, taskWorkers } : thread]
      : []
  })
}

/** True when the empty sidebar should mention its filters. */
export function hasActiveFilters(filters: SidebarFilters): boolean {
  return (
    filters.sources.length > 0 ||
    filters.includeAutomations !== DEFAULT_SIDEBAR_FILTERS.includeAutomations ||
    filters.includeResolved !== DEFAULT_SIDEBAR_FILTERS.includeResolved ||
    filters.includeSlack !== DEFAULT_SIDEBAR_FILTERS.includeSlack ||
    filters.ownedOnly
  )
}
