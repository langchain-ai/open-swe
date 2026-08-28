import type { SidebarThreadLocation } from "./sidebarThreads"
import type { AgentSource, AgentStatus, AgentThread } from "./types"

export type PrFilter = "none" | "draft" | "open" | "merged" | "closed"

export interface SidebarFilters {
  statuses: Array<AgentStatus>
  sources: Array<AgentSource>
  locations: Array<SidebarThreadLocation>
  pr: Array<PrFilter>
  models: Array<string>
  includeAutomations: boolean
  includeResolved: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  statuses: [],
  sources: [],
  locations: [],
  pr: [],
  models: [],
  includeAutomations: false,
  includeResolved: false,
}

export const STATUS_FILTER_OPTIONS: Array<{
  value: AgentStatus
  label: string
}> = [
  { value: "running", label: "Running" },
  { value: "finished", label: "Finished" },
  { value: "interrupted", label: "Interrupted" },
  { value: "error", label: "Error" },
  { value: "idle", label: "Idle" },
]

export const SOURCE_FILTER_OPTIONS: Array<{
  value: AgentSource
  label: string
}> = [
  { value: "dashboard", label: "Dashboard" },
  { value: "github", label: "GitHub" },
  { value: "slack", label: "Slack" },
  { value: "linear", label: "Linear" },
  { value: "schedule", label: "Schedule" },
]

export const LOCATION_FILTER_OPTIONS: Array<{
  value: SidebarThreadLocation
  label: string
}> = [
  { value: "cloud", label: "Cloud" },
  { value: "local", label: "This Mac" },
]

export const PR_FILTER_OPTIONS: Array<{ value: PrFilter; label: string }> = [
  { value: "none", label: "No pull request" },
  { value: "draft", label: "Draft" },
  { value: "open", label: "Open" },
  { value: "merged", label: "Merged" },
  { value: "closed", label: "Closed" },
]

interface FilterableThread {
  status: AgentStatus
  source?: AgentSource
  location?: SidebarThreadLocation
  threadCategory?: string
  pr?: AgentThread["pr"]
  model: string
}

function threadSource(thread: FilterableThread): AgentSource {
  return thread.source ?? "dashboard"
}

function threadPr(thread: FilterableThread): PrFilter {
  return thread.pr ? thread.pr.state : "none"
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
    if (
      !filters.includeAutomations &&
      isAutomationThread(thread) &&
      !filters.sources.includes("schedule")
    ) {
      return false
    }
    if (
      filters.statuses.length > 0 &&
      !filters.statuses.includes(thread.status)
    ) {
      return false
    }
    if (
      filters.sources.length > 0 &&
      !filters.sources.includes(threadSource(thread))
    ) {
      return false
    }
    if (
      filters.locations.length > 0 &&
      !filters.locations.includes(thread.location ?? "cloud")
    ) {
      return false
    }
    if (filters.pr.length > 0 && !filters.pr.includes(threadPr(thread))) {
      return false
    }
    if (filters.models.length > 0 && !filters.models.includes(thread.model)) {
      return false
    }
    return true
  })
}

export interface SidebarFacets {
  models: Array<string>
}

/** Distinct model values present in the given threads. */
export function availableFacets<T extends FilterableThread>(
  threads: Array<T>
): SidebarFacets {
  const models = new Set<string>()
  for (const thread of threads) {
    if (thread.model) models.add(thread.model)
  }
  return {
    models: [...models].sort((a, b) => a.localeCompare(b)),
  }
}

/** True when any filter dimension differs from the defaults. */
export function hasActiveFilters(filters: SidebarFilters): boolean {
  return (
    filters.statuses.length > 0 ||
    filters.sources.length > 0 ||
    filters.locations.length > 0 ||
    filters.pr.length > 0 ||
    filters.models.length > 0 ||
    filters.includeAutomations !== DEFAULT_SIDEBAR_FILTERS.includeAutomations ||
    filters.includeResolved !== DEFAULT_SIDEBAR_FILTERS.includeResolved
  )
}

/** Toggle membership of a value within a filter array (immutable). */
export function toggleArrayValue<T>(values: Array<T>, value: T): Array<T> {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value]
}
