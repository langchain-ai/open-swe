import { groupThreads } from "./api"
import type { AgentSource, AgentStatus, AgentThread } from "./types"

export type SidebarGroupMode = "none" | "date" | "status" | "repo"

export type SidebarOwnership = "all" | "mine" | "shared"

export type PrFilter = "none" | "draft" | "open" | "merged" | "closed"

export interface SidebarFilters {
  ownership: SidebarOwnership
  statuses: Array<AgentStatus>
  sources: Array<AgentSource>
  pr: Array<PrFilter>
  models: Array<string>
  repos: Array<string>
  includeResolved: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  ownership: "all",
  statuses: [],
  sources: [],
  pr: [],
  models: [],
  repos: [],
  includeResolved: true,
}

export const GROUP_MODE_OPTIONS: Array<{
  value: SidebarGroupMode
  label: string
}> = [
  { value: "repo", label: "Project" },
  { value: "date", label: "Date" },
  { value: "status", label: "Status" },
  { value: "none", label: "None" },
]

export const OWNERSHIP_OPTIONS: Array<{
  value: SidebarOwnership
  label: string
}> = [
  { value: "all", label: "All agents" },
  { value: "mine", label: "My agents" },
  { value: "shared", label: "Shared with me" },
]

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

export const PR_FILTER_OPTIONS: Array<{ value: PrFilter; label: string }> = [
  { value: "none", label: "No pull request" },
  { value: "draft", label: "Draft" },
  { value: "open", label: "Open" },
  { value: "merged", label: "Merged" },
  { value: "closed", label: "Closed" },
]

function threadSource(thread: AgentThread): AgentSource {
  return thread.source ?? "dashboard"
}

function threadPr(thread: AgentThread): PrFilter {
  return thread.pr ? thread.pr.state : "none"
}

/** Apply the active filter dimensions to a list of threads. */
export function filterThreads(
  threads: Array<AgentThread>,
  filters: SidebarFilters
): Array<AgentThread> {
  return threads.filter((thread) => {
    if (filters.ownership === "mine" && thread.isOwner === false) return false
    if (filters.ownership === "shared" && thread.isOwner !== false) return false
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
    if (filters.pr.length > 0 && !filters.pr.includes(threadPr(thread))) {
      return false
    }
    if (filters.models.length > 0 && !filters.models.includes(thread.model)) {
      return false
    }
    if (
      filters.repos.length > 0 &&
      !filters.repos.includes(thread.repoFullName)
    ) {
      return false
    }
    return true
  })
}

export interface SidebarFacets {
  models: Array<string>
  repos: Array<string>
}

/** Distinct model + repo values present in the given threads (for the filter submenus). */
export function availableFacets(threads: Array<AgentThread>): SidebarFacets {
  const models = new Set<string>()
  const repos = new Set<string>()
  for (const thread of threads) {
    if (thread.model) models.add(thread.model)
    if (thread.repoFullName) repos.add(thread.repoFullName)
  }
  return {
    models: [...models].sort((a, b) => a.localeCompare(b)),
    repos: [...repos].sort((a, b) => a.localeCompare(b)),
  }
}

export interface ThreadGroupSection {
  key: string
  label: string
  threads: Array<AgentThread>
  defaultCollapsed: boolean
}

const STATUS_GROUP_ORDER: Array<AgentStatus> = [
  "running",
  "finished",
  "interrupted",
  "error",
  "idle",
]

const STATUS_GROUP_LABEL: Record<AgentStatus, string> = {
  running: "Running",
  finished: "Finished",
  interrupted: "Interrupted",
  error: "Error",
  idle: "Idle",
}

function sortedByRecency(threads: Array<AgentThread>): Array<AgentThread> {
  return [...threads].sort((a, b) => b.updatedAt - a.updatedAt)
}

/** Split threads into ordered, labelled sections according to the group mode. */
export function groupThreadsByMode(
  threads: Array<AgentThread>,
  mode: SidebarGroupMode
): Array<ThreadGroupSection> {
  if (threads.length === 0) return []

  if (mode === "none") {
    return [
      {
        key: "all",
        label: "All",
        threads: sortedByRecency(threads),
        defaultCollapsed: false,
      },
    ]
  }

  if (mode === "date") {
    const groups = groupThreads(threads)
    return (
      [
        {
          key: "today",
          label: "Today",
          threads: groups.today,
          collapsed: false,
        },
        {
          key: "last7",
          label: "Last 7 days",
          threads: groups.last7,
          collapsed: true,
        },
        {
          key: "last30",
          label: "Last 30 days",
          threads: groups.last30,
          collapsed: true,
        },
        {
          key: "older",
          label: "Older",
          threads: groups.older,
          collapsed: false,
        },
      ] as const
    )
      .filter((section) => section.threads.length > 0)
      .map((section) => ({
        key: section.key,
        label: section.label,
        threads: section.threads,
        defaultCollapsed: section.collapsed,
      }))
  }

  if (mode === "status") {
    const byStatus = new Map<AgentStatus, Array<AgentThread>>()
    for (const thread of threads) {
      const list = byStatus.get(thread.status) ?? []
      list.push(thread)
      byStatus.set(thread.status, list)
    }
    return STATUS_GROUP_ORDER.filter((status) => byStatus.has(status)).map(
      (status) => ({
        key: status,
        label: STATUS_GROUP_LABEL[status],
        threads: sortedByRecency(byStatus.get(status) ?? []),
        defaultCollapsed: false,
      })
    )
  }

  const byRepo = new Map<string, Array<AgentThread>>()
  for (const thread of threads) {
    const key = thread.repoFullName || "No repository"
    const list = byRepo.get(key) ?? []
    list.push(thread)
    byRepo.set(key, list)
  }
  return [...byRepo.keys()]
    .sort((a, b) => a.localeCompare(b))
    .map((repo) => ({
      key: repo,
      label: repo,
      threads: sortedByRecency(byRepo.get(repo) ?? []),
      defaultCollapsed: false,
    }))
}

/** True when any filter dimension differs from the defaults. */
export function hasActiveFilters(filters: SidebarFilters): boolean {
  return (
    filters.ownership !== DEFAULT_SIDEBAR_FILTERS.ownership ||
    filters.statuses.length > 0 ||
    filters.sources.length > 0 ||
    filters.pr.length > 0 ||
    filters.models.length > 0 ||
    filters.repos.length > 0 ||
    filters.includeResolved !== DEFAULT_SIDEBAR_FILTERS.includeResolved
  )
}

/** Toggle membership of a value within a filter array (immutable). */
export function toggleArrayValue<T>(values: Array<T>, value: T): Array<T> {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value]
}
