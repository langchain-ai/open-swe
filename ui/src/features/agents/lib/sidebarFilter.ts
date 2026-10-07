import type { AgentSource } from "./types"

export interface SidebarFilters {
  sources: Array<AgentSource>
  includeAutomations: boolean
  includeResolved: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  sources: [],
  includeAutomations: false,
  includeResolved: false,
}

interface FilterableThread {
  source?: AgentSource
  threadCategory?: string
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
  filters: SidebarFilters
): Array<T> {
  const matches = (thread: FilterableThread) =>
    (filters.includeAutomations ||
      !isAutomationThread(thread) ||
      filters.sources.includes("schedule")) &&
    (filters.sources.length === 0 ||
      filters.sources.includes(threadSource(thread)))
  return threads.flatMap((thread) => {
    if (matches(thread)) return [thread]
    const taskWorkers = thread.taskWorkers?.filter(matches)
    return taskWorkers?.length ? [{ ...thread, taskWorkers }] : []
  })
}

/** True when any filter dimension differs from the defaults. */
export function hasActiveFilters(filters: SidebarFilters): boolean {
  return (
    filters.sources.length > 0 ||
    filters.includeAutomations !== DEFAULT_SIDEBAR_FILTERS.includeAutomations ||
    filters.includeResolved !== DEFAULT_SIDEBAR_FILTERS.includeResolved
  )
}
