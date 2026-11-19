import {
  invalidationTopic,
  type InvalidationTopic,
} from "@/lib/invalidations/topics"
import type {
  AgentSource,
  AgentStatus,
  AgentSubagentSummary,
  AgentThread,
  ReviewPageRef,
} from "./types"

export interface SidebarThreadItem {
  key: string
  id: string
  title: string
  repoKey: string | null
  repoLabel: string | null
  model: string
  source?: AgentSource
  ownerId?: string | null
  threadCategory?: string
  status: AgentStatus
  viewed: boolean
  resolved?: boolean
  createdAt: number
  updatedAt: number
  planStatus?: string | null
  pr?: AgentThread["pr"]
  /** `owner/repo` + number of `pr`, when the thread carries a full PR record. */
  prRef?: { repoFullName: string; number: number }
  reviewPage?: ReviewPageRef
  /** Subagents the thread spawned, listed as sub-threads under its row. */
  subagents?: Array<AgentSubagentSummary>
  taskWorkers?: Array<AgentThread>
  waitingForDelegatedWork?: boolean
  thread: AgentThread
}

export interface SidebarRepoOption {
  key: string
  label: string
}

export interface SidebarRepoGroup extends SidebarRepoOption {
  threads: Array<SidebarThreadItem>
}

export const DEFAULT_SIDEBAR_WORKSPACE_SLUG = "default"

/**
 * Identity, not display name: `owner/repo` for cloud and the checkout path for
 * local. Keying on the short label instead would merge `acme/api` with
 * `other/api`, and two local repos both called `api`, into one folder that
 * cannot be told apart.
 */
export function sidebarRepoKey(identity?: string | null): string | null {
  const normalized = identity?.trim().toLowerCase()
  return normalized ? `repo:${normalized}` : null
}

function pullRequestRef(
  thread: AgentThread
): { repoFullName: string; number: number } | undefined {
  const latest = thread.pullRequests?.at(-1)
  if (latest) {
    return { repoFullName: latest.repoFullName, number: latest.number }
  }
  if (!thread.pr || thread.repoFullName.split("/").length !== 2)
    return undefined
  return { repoFullName: thread.repoFullName, number: thread.pr.number }
}

export function cloudSidebarThread(thread: AgentThread): SidebarThreadItem {
  const repoLabel = thread.repo.trim() || null
  return {
    key: `cloud:${thread.id}`,
    id: thread.id,
    title: thread.title,
    repoKey: sidebarRepoKey(thread.repoFullName.trim() || repoLabel),
    repoLabel,
    model: thread.model,
    source: thread.source,
    ownerId: thread.ownerId,
    threadCategory: thread.threadCategory,
    status: thread.status,
    viewed: thread.viewed,
    resolved: thread.resolved,
    createdAt: Math.max(
      thread.createdAt,
      ...(thread.taskWorkers ?? []).map((worker) => worker.createdAt)
    ),
    updatedAt: Math.max(
      thread.updatedAt,
      ...(thread.taskWorkers ?? []).map((worker) => worker.updatedAt)
    ),
    planStatus: thread.planStatus,
    pr: thread.pr,
    prRef: pullRequestRef(thread),
    reviewPage: thread.reviewPage,
    subagents: thread.subagents,
    taskWorkers: thread.taskWorkers,
    waitingForDelegatedWork: thread.waitingForDelegatedWork,
    thread,
  }
}

export type SidebarSort = "created" | "updated" | "manual"

function byRecency(left: SidebarThreadItem, right: SidebarThreadItem): number {
  return (
    right.updatedAt - left.updatedAt ||
    right.createdAt - left.createdAt ||
    left.key.localeCompare(right.key)
  )
}

function byCreation(left: SidebarThreadItem, right: SidebarThreadItem): number {
  return (
    right.createdAt - left.createdAt ||
    right.updatedAt - left.updatedAt ||
    left.key.localeCompare(right.key)
  )
}

export function sortSidebarThreads(
  threads: ReadonlyArray<SidebarThreadItem>,
  mode: SidebarSort = "updated"
): Array<SidebarThreadItem> {
  // "manual" keeps the order the caller supplied — for pins that is the stored
  // pin order, which is the only manual ordering the user can actually set.
  if (mode === "manual") return [...threads]
  return [...threads].sort(mode === "created" ? byCreation : byRecency)
}

export interface SidebarThreadGroup {
  key: string
  label: string
  threads: Array<SidebarThreadItem>
  mergable?: boolean
}

/** The past seven calendar days stay separate; older activity uses broader ranges. */
export function groupSidebarThreadsByRecency(
  threads: ReadonlyArray<SidebarThreadItem>,
  now: Date = new Date()
): Array<SidebarThreadGroup> {
  let remaining = sortSidebarThreads(threads)

  type Range = {
    key: string
    label: string
    cutoff: number
    mergable?: boolean
  }
  const ranges: Range[] = []

  const today = new Date(now).setHours(0, 0, 0, 0)
  const last12Hours = now.getTime() - 12 * 60 * 60 * 1000

  if (
    last12Hours > today &&
    remaining.some((thread) => thread.updatedAt >= last12Hours) &&
    remaining.some(
      (thread) => thread.updatedAt >= today && thread.updatedAt < last12Hours
    )
  ) {
    ranges.push({
      key: "last-12-hours",
      label: "Last 12 hours",
      cutoff: last12Hours,
    })
  }

  ranges.push(
    { key: "today", label: "Today", cutoff: today },
    {
      key: "yesterday",
      label: "Yesterday",
      cutoff: new Date(today).setDate(now.getDate() - 1),
    }
  )

  const weekDay = (now.getDay() + 6) % 7

  for (let offset = 2; offset <= 6; offset++) {
    const day = new Date(today)
    day.setDate(day.getDate() - offset)
    ranges.push({
      key: `day:${day.getFullYear()}-${day.getMonth() + 1}-${day.getDate()}`,
      label: day.toLocaleDateString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        year: day.getFullYear() === now.getFullYear() ? undefined : "numeric",
      }),
      cutoff: day.getTime(),
    })
  }

  const weekStart = new Date(today)
  weekStart.setDate(weekStart.getDate() - weekDay)

  ranges.push(
    {
      key: "last-week",
      label: "Last week",
      cutoff: new Date(weekStart).setDate(weekStart.getDate() - 7),
      mergable: true,
    },
    {
      key: "last-14-days",
      label: "Last 14 days",
      cutoff: new Date(today).setDate(now.getDate() - 13),
      mergable: true,
    },
    { key: "older", label: "Older", cutoff: -Infinity, mergable: true }
  )

  const groups: SidebarThreadGroup[] = []
  for (const range of ranges) {
    const cutoff = range.cutoff
    const items = remaining.filter((thread) => thread.updatedAt >= cutoff)
    if (!items.length) continue

    if (range.mergable) {
      // Only older ranges are adaptive; calendar days keep their own sections.
      if (items.length < 3 && items.length < remaining.length) continue

      const previous = groups.at(-1)

      if (items.length < 3 && previous?.mergable) {
        groups.pop()
        items.unshift(...previous.threads)
      }
    }

    groups.push({ ...range, threads: items })

    remaining = remaining.filter((thread) => thread.updatedAt < cutoff)
    if (!remaining.length) break
  }

  const onlyDateGroup = groups.length === 1 ? groups[0] : undefined
  if (onlyDateGroup?.key === "older") onlyDateGroup.label = "Recents"

  return groups
}

export function threadFamily(thread: AgentThread): Array<AgentThread> {
  return [thread, ...(thread.taskWorkers ?? [])]
}

/**
 * One sidebar row for a task family seen through snapshots of differing age:
 * `row` (usually the listed one, which honors the sidebar's filters) and
 * `details` of the coordinator or an open worker, which poll on their own
 * schedules. Each member keeps its newest copy, and a worker the row has not
 * caught up with yet joins it unless it is archived.
 */
export function mergeTaskFamily(
  row: AgentThread,
  details: ReadonlyArray<AgentThread>
): AgentThread {
  const members = new Map(
    threadFamily(row).map((member) => [member.id, member])
  )
  for (const member of details.flatMap(threadFamily)) {
    const known = members.get(member.id)
    if (known ? member.updatedAt >= known.updatedAt : !member.resolved) {
      members.set(member.id, member)
    }
  }
  const [root = row, ...workers] = members.values()
  return root.taskWorkers || workers.length
    ? { ...root, taskWorkers: workers }
    : root
}

export function withoutNestedWorkers(
  threads: Array<AgentThread>
): Array<AgentThread> {
  const nestedIds = new Set(
    threads
      .flatMap((thread) => thread.taskWorkers ?? [])
      .map((worker) => worker.id)
  )
  return threads.filter((thread) => !nestedIds.has(thread.id))
}

export function sidebarItemContains(
  item: SidebarThreadItem,
  key: string | undefined
): boolean {
  return (
    item.key === key ||
    (item.taskWorkers ?? []).some((worker) => `cloud:${worker.id}` === key)
  )
}

export function sidebarRefreshInterval(
  threads: ReadonlyArray<AgentThread> = []
): number | false {
  const members = threads.flatMap(threadFamily)
  if (members.some((member) => member.status === "running")) return 2000
  return members.some((member) => member.pendingWorkflowApproval)
    ? 30_000
    : false
}

/** What tells a sidebar list that a thread it shows gained or lost a worker, its first included. */
export function taskWorkerTopics(
  threads: ReadonlyArray<AgentThread> = []
): Array<InvalidationTopic> {
  return threads
    .filter((thread) => thread.taskMembership?.role !== "worker")
    .map((thread) => invalidationTopic("task-workers", thread.id))
}

export function pinnedThreadShortcuts(
  threads: Array<AgentThread>
): Array<AgentThread> {
  const pinnedIds = new Set(threads.map((thread) => thread.id))
  return threads.map((thread) =>
    thread.taskWorkers
      ? {
          ...thread,
          taskWorkers: thread.taskWorkers.filter(
            (worker) => !pinnedIds.has(worker.id)
          ),
        }
      : thread
  )
}
