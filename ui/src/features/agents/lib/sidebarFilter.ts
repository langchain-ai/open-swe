import { groupThreads } from "./api"
import { groupThreadsForView } from "./threadViews"
import type { AgentSource, AgentStatus, AgentThread } from "./types"

export type SidebarGroupMode = "none" | "focus" | "date" | "repo"

export type SidebarOwnership = "all" | "mine" | "shared"

export type PrFilter = "none" | "draft" | "open" | "merged" | "closed"

export interface SidebarFilters {
  ownership: SidebarOwnership
  statuses: Array<AgentStatus>
  sources: Array<AgentSource>
  pr: Array<PrFilter>
  models: Array<string>
  repos: Array<string>
  includeAutomations: boolean
  includeResolved: boolean
}

export const DEFAULT_SIDEBAR_FILTERS: SidebarFilters = {
  ownership: "all",
  statuses: [],
  sources: [],
  pr: [],
  models: [],
  repos: [],
  includeAutomations: false,
  includeResolved: false,
}

export const GROUP_MODE_OPTIONS: Array<{
  value: SidebarGroupMode
  label: string
}> = [
  { value: "focus", label: "Focus" },
  { value: "repo", label: "Project" },
  { value: "date", label: "Date" },
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

function isAutomationThread(thread: AgentThread): boolean {
  return (
    thread.threadCategory === "automation" ||
    threadSource(thread) === "schedule"
  )
}

/** Apply the active filter dimensions to a list of threads. */
export function filterThreads(
  threads: Array<AgentThread>,
  filters: SidebarFilters
): Array<AgentThread> {
  return threads.filter((thread) => {
    if (
      !filters.includeAutomations &&
      isAutomationThread(thread) &&
      !filters.sources.includes("schedule")
    ) {
      return false
    }
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

export function reconcilePinnedAttentionThread(
  pinnedThread: AgentThread | undefined,
  activeThreadId: string | undefined,
  activeAttentionThread: AgentThread | undefined
): AgentThread | undefined {
  if (pinnedThread?.id === activeThreadId) return pinnedThread
  return activeAttentionThread
}

function sortedByCreation(threads: Array<AgentThread>): Array<AgentThread> {
  return [...threads].sort((a, b) => b.createdAt - a.createdAt)
}

/** Split threads into ordered, labelled sections according to the group mode. */
export function groupThreadsByMode(
  threads: Array<AgentThread>,
  mode: SidebarGroupMode,
  pinnedThread?: AgentThread
): Array<ThreadGroupSection> {
  if (threads.length === 0) return []

  if (mode === "none") {
    return [
      {
        key: "all",
        label: "All",
        threads: sortedByCreation(threads),
        defaultCollapsed: false,
      },
    ]
  }

  if (mode === "focus") {
    const currentPinnedThread = pinnedThread
      ? threads.find((thread) => thread.id === pinnedThread.id)
      : undefined
    const groupedThreads =
      pinnedThread && currentPinnedThread
        ? threads.map((thread) =>
            thread.id === pinnedThread.id
              ? { ...thread, viewed: pinnedThread.viewed }
              : thread
          )
        : threads
    return groupThreadsForView(groupedThreads, "focus").map((group) => ({
      ...group,
      threads: sortedByCreation(group.threads).map((thread) =>
        thread.id === currentPinnedThread?.id ? currentPinnedThread : thread
      ),
      defaultCollapsed: false,
    }))
  }

  if (mode === "date") {
    const groups = groupThreads(threads, "createdAt")
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
          collapsed: false,
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
      threads: sortedByCreation(byRepo.get(repo) ?? []),
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
