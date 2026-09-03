import type { AgentSource, AgentStatus, AgentThread } from "./types"

export type ThreadsLayout = "board" | "list"
export type ThreadGrouping =
  | "focus"
  | "status"
  | "repo"
  | "source"
  | "pr"
  | "none"

export interface ThreadViewGroup {
  key: string
  label: string
  threads: Array<AgentThread>
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

const FOCUS_GROUPS = [
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

function focusKey(thread: AgentThread): string {
  if (thread.resolved) return "done"
  if (thread.status === "running") return "progress"
  if (
    thread.status === "error" ||
    thread.status === "interrupted" ||
    thread.planStatus === "ready" ||
    thread.planStatus === "shared" ||
    thread.attentionReason != null ||
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
  definitions: Array<{ key: string; label: string }>,
  threads: Array<AgentThread>,
  keyFor: (thread: AgentThread) => string
): Array<ThreadViewGroup> {
  const grouped = new Map<string, Array<AgentThread>>()
  for (const thread of threads) {
    const key = keyFor(thread)
    grouped.set(key, [...(grouped.get(key) ?? []), thread])
  }
  return definitions
    .filter(({ key }) => grouped.has(key))
    .map(({ key, label }) => ({
      key,
      label,
      threads: sortThreads(grouped.get(key) ?? []),
    }))
}

export function groupThreadsForView(
  threads: Array<AgentThread>,
  grouping: ThreadGrouping
): Array<ThreadViewGroup> {
  if (threads.length === 0) return []
  if (grouping === "none") {
    return [{ key: "all", label: "All threads", threads: sortThreads(threads) }]
  }
  if (grouping === "focus") {
    return buildGroups(FOCUS_GROUPS, threads, focusKey)
  }
  if (grouping === "status") {
    return buildGroups(
      STATUS_ORDER.map((key) => ({ key, label: STATUS_LABELS[key] })),
      threads,
      (thread) => thread.status
    )
  }
  if (grouping === "source") {
    return buildGroups(
      SOURCE_ORDER.map((key) => ({ key, label: SOURCE_LABELS[key] })),
      threads,
      (thread) => thread.source ?? "dashboard"
    )
  }
  if (grouping === "pr") {
    return buildGroups(
      PR_GROUPS,
      threads,
      (thread) => thread.pr?.state ?? "none"
    )
  }
  const labels = new Set(
    threads.map((thread) => thread.repoFullName || "No repository")
  )
  const definitions = [...labels]
    .sort((left, right) => left.localeCompare(right))
    .map((label) => ({ key: label, label }))
  return buildGroups(
    definitions,
    threads,
    (thread) => thread.repoFullName || "No repository"
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
