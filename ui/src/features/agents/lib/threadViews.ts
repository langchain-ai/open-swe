import type {
  AgentSource,
  AgentStatus,
  AgentThread,
  ThreadFocusColumn,
} from "./types"

export type ThreadsLayout = "board" | "list"
export type ThreadGrouping =
  "focus" | "status" | "repo" | "source" | "pr" | "none"

export interface ThreadViewGroup {
  key: string
  label: string
  threads: Array<AgentThread>
}

interface ThreadGroupDefinition {
  key: string
  label: string
}

export const THREAD_GROUPING_OPTIONS: Array<{
  value: ThreadGrouping
  label: string
}> = [
  { value: "focus", label: "Focus" },
  { value: "status", label: "Status" },
  { value: "repo", label: "Repository" },
  { value: "source", label: "Source" },
  { value: "pr", label: "Pull request" },
  { value: "none", label: "None" },
]

export const THREAD_FOCUS_GROUPS: Array<{
  key: ThreadFocusColumn
  label: string
}> = [
  { key: "attention", label: "Needs attention" },
  { key: "progress", label: "In progress" },
  { key: "ready", label: "Ready" },
  { key: "done", label: "Done" },
]

const STATUS_ORDER: Array<AgentStatus> = [
  "running",
  "finished",
  "interrupted",
  "error",
  "idle",
]

const STATUS_LABELS: Record<AgentStatus, string> = {
  running: "Running",
  finished: "Finished",
  interrupted: "Interrupted",
  error: "Error",
  idle: "Idle",
}

const SOURCE_ORDER: Array<AgentSource> = [
  "dashboard",
  "github",
  "slack",
  "linear",
  "schedule",
]

const SOURCE_LABELS: Record<AgentSource, string> = {
  dashboard: "Dashboard",
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
  schedule: "Schedule",
}

const PR_GROUPS = [
  { key: "none", label: "No pull request" },
  { key: "draft", label: "Draft" },
  { key: "open", label: "Open" },
  { key: "merged", label: "Merged" },
  { key: "closed", label: "Closed" },
]

function focusKey(thread: AgentThread): ThreadFocusColumn {
  if (thread.resolved) return "done"
  if (thread.boardFocusState) return thread.boardFocusState
  if (thread.status === "running") return "progress"
  if (
    thread.status === "error" ||
    thread.status === "interrupted" ||
    thread.planStatus === "ready" ||
    thread.planStatus === "shared" ||
    (thread.status === "finished" && !thread.viewed)
  ) {
    return "attention"
  }
  return "ready"
}

function sortThreads(threads: Array<AgentThread>): Array<AgentThread> {
  return [...threads].sort((left, right) => right.updatedAt - left.updatedAt)
}

function buildGroups(
  definitions: Array<ThreadGroupDefinition>,
  threads: Array<AgentThread>,
  keyFor: (thread: AgentThread) => string,
  includeEmpty: boolean
): Array<ThreadViewGroup> {
  const grouped = new Map<string, Array<AgentThread>>()
  for (const thread of threads) {
    const key = keyFor(thread)
    grouped.set(key, [...(grouped.get(key) ?? []), thread])
  }
  return definitions
    .filter(({ key }) => includeEmpty || grouped.has(key))
    .map(({ key, label }) => ({
      key,
      label,
      threads: sortThreads(grouped.get(key) ?? []),
    }))
}

export function threadGroupDefinitions(
  threads: Array<AgentThread>,
  grouping: ThreadGrouping
): Array<ThreadGroupDefinition> {
  if (grouping === "focus") return THREAD_FOCUS_GROUPS
  if (grouping === "status") {
    return STATUS_ORDER.map((key) => ({ key, label: STATUS_LABELS[key] }))
  }
  if (grouping === "source") {
    return SOURCE_ORDER.map((key) => ({ key, label: SOURCE_LABELS[key] }))
  }
  if (grouping === "pr") return PR_GROUPS
  if (grouping === "none") return [{ key: "all", label: "All threads" }]
  return [
    ...new Set(threads.map((thread) => thread.repoFullName || "No repository")),
  ]
    .sort((left, right) => left.localeCompare(right))
    .map((label) => ({ key: label, label }))
}

export function groupThreadsForView(
  threads: Array<AgentThread>,
  grouping: ThreadGrouping,
  options: { includeEmpty?: boolean } = {}
): Array<ThreadViewGroup> {
  if (threads.length === 0 && !options.includeEmpty) return []
  const definitions = threadGroupDefinitions(threads, grouping)
  if (grouping === "none") {
    return [{ key: "all", label: "All threads", threads: sortThreads(threads) }]
  }
  if (grouping === "focus") {
    return buildGroups(
      definitions,
      threads,
      focusKey,
      options.includeEmpty === true
    )
  }
  if (grouping === "status") {
    return buildGroups(
      definitions,
      threads,
      (thread) => thread.status,
      options.includeEmpty === true
    )
  }
  if (grouping === "source") {
    return buildGroups(
      definitions,
      threads,
      (thread) => thread.source ?? "dashboard",
      options.includeEmpty === true
    )
  }
  if (grouping === "pr") {
    return buildGroups(
      definitions,
      threads,
      (thread) => thread.pr?.state ?? "none",
      options.includeEmpty === true
    )
  }
  return buildGroups(
    definitions,
    threads,
    (thread) => thread.repoFullName || "No repository",
    options.includeEmpty === true
  )
}

export function parseColumnOrder(value?: string): Array<string> {
  return value?.split("|").filter(Boolean) ?? []
}

export function reconcileColumnOrder(
  defaultKeys: Array<string>,
  savedKeys: Array<string>
): Array<string> {
  const available = new Set(defaultKeys)
  const seen = new Set<string>()
  const saved = savedKeys.filter((key) => {
    if (!available.has(key) || seen.has(key)) return false
    seen.add(key)
    return true
  })
  return [...saved, ...defaultKeys.filter((key) => !seen.has(key))]
}

export function parseHiddenColumns(value?: string): Array<string> {
  return value?.split("|").filter(Boolean) ?? []
}

export function reconcileHiddenColumns(
  availableKeys: Array<string>,
  savedKeys: Array<string>
): Array<string> {
  const available = new Set(availableKeys)
  const seen = new Set<string>()
  return savedKeys.filter((key) => {
    if (!available.has(key) || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function moveColumn(
  order: Array<string>,
  key: string,
  direction: -1 | 1
): Array<string> {
  const index = order.indexOf(key)
  const target = index + direction
  if (index < 0 || target < 0 || target >= order.length) return order
  const next = [...order]
  ;[next[index], next[target]] = [next[target]!, next[index]!]
  return next
}

export function moveColumnBefore(
  order: Array<string>,
  draggedKey: string,
  targetKey: string
): Array<string> {
  if (draggedKey === targetKey) return order
  const draggedIndex = order.indexOf(draggedKey)
  const targetIndex = order.indexOf(targetKey)
  if (draggedIndex < 0 || targetIndex < 0) return order
  const next = [...order]
  next.splice(draggedIndex, 1)
  const insertionIndex =
    draggedIndex < targetIndex ? targetIndex - 1 : targetIndex
  next.splice(insertionIndex, 0, draggedKey)
  return next
}
