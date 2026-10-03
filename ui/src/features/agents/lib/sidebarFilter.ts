import type { AgentSource } from "./types"

export interface SidebarFilters {
  sources: Array<AgentSource>
  includeAutomations: boolean
  includeResolved: boolean
  hideSlackWithoutCodeChanges: boolean
  hideSlackThreads: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  sources: [],
  includeAutomations: false,
  includeResolved: false,
  hideSlackWithoutCodeChanges: true,
  hideSlackThreads: false,
}

interface FilterableThread {
  source?: AgentSource
  threadCategory?: string
  hasEdits?: boolean | null
  pr?: object
  pullRequests?: ReadonlyArray<unknown>
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
  return threads.filter((thread) => {
    if (filters.hideSlackThreads && threadSource(thread) === "slack")
      return false
    if (
      filters.hideSlackWithoutCodeChanges &&
      threadSource(thread) === "slack" &&
      thread.hasEdits === false &&
      !thread.pr &&
      !thread.pullRequests?.length
    )
      return false
    if (
      !filters.includeAutomations &&
      isAutomationThread(thread) &&
      !filters.sources.includes("schedule")
    ) {
      return false
    }
    if (
      filters.sources.length > 0 &&
      !filters.sources.includes(threadSource(thread))
    ) {
      return false
    }
    return true
  })
}

/** True when any filter dimension differs from the defaults. */
export function hasActiveFilters(filters: SidebarFilters): boolean {
  return (
    filters.sources.length > 0 ||
    filters.hideSlackThreads !== DEFAULT_SIDEBAR_FILTERS.hideSlackThreads ||
    filters.includeAutomations !== DEFAULT_SIDEBAR_FILTERS.includeAutomations ||
    filters.includeResolved !== DEFAULT_SIDEBAR_FILTERS.includeResolved ||
    filters.hideSlackWithoutCodeChanges !==
      DEFAULT_SIDEBAR_FILTERS.hideSlackWithoutCodeChanges
  )
}
