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

/** A repo option annotated with the workspace slug that owns its repository. */
export type SidebarWorkspacedRepoOption = SidebarRepoOption & {
  workspace?: string
}

export const DEFAULT_SIDEBAR_WORKSPACE_SLUG = "default"

export interface SidebarWorkspaceGroup<
  TRepo extends SidebarRepoOption = SidebarRepoGroup,
> {
  slug: string
  name: string
  repos: Array<TRepo>
}

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

/**
 * Buckets already-built repo groups by the workspace that owns them, using
 * each entry's `workspace` field from `repos` — falling back to
 * {@link DEFAULT_SIDEBAR_WORKSPACE_SLUG} for a repo with no workspace
 * (a repository is preferred by at most one workspace, and one no workspace
 * prefers is annotated `default`, so this only fires for a repo the caller
 * didn't annotate). A workspace absent from `workspaces`
 * displays its slug as its own name.
 *
 * Generic over the repo-group shape so callers with richer, hydrated
 * groups (live thread counts, active-thread hints) can bucket those directly
 * instead of rebuilding them from raw threads.
 */
export function groupRepoGroupsByWorkspace<TRepo extends SidebarRepoOption>(
  groups: ReadonlyArray<TRepo>,
  repos: ReadonlyArray<SidebarWorkspacedRepoOption>,
  workspaces: ReadonlyArray<{ slug: string; name: string }>
): Array<SidebarWorkspaceGroup<TRepo>> {
  const workspaceByRepoKey = new Map(
    repos.map((repo) => [
      repo.key,
      repo.workspace ?? DEFAULT_SIDEBAR_WORKSPACE_SLUG,
    ])
  )
  const names = new Map(
    workspaces.map((workspace) => [workspace.slug, workspace.name])
  )
  const buckets = new Map<string, SidebarWorkspaceGroup<TRepo>>()
  for (const group of groups) {
    const slug =
      workspaceByRepoKey.get(group.key) ?? DEFAULT_SIDEBAR_WORKSPACE_SLUG
    const bucket = buckets.get(slug) ?? {
      slug,
      name: names.get(slug) ?? slug,
      repos: [],
    }
    bucket.repos.push(group)
    buckets.set(slug, bucket)
  }
  return [...buckets.values()]
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
}

/** Running work follows at most four adaptive activity-date sections. */
export function groupSidebarThreadsByDate(
  threads: ReadonlyArray<SidebarThreadItem>,
  now: Date = new Date()
): Array<SidebarThreadGroup> {
  const today = new Date(now).setHours(0, 0, 0, 0)
  const last12Hours = now.getTime() - 12 * 60 * 60 * 1000
  const ranges = [
    { key: "last-12-hours", label: "Last 12 hours", cutoff: last12Hours },
    // Before noon, the rolling window already extends beyond Today.
    ...(last12Hours > today
      ? [{ key: "today", label: "Today", cutoff: today }]
      : []),
    {
      key: "yesterday",
      label: "Yesterday",
      cutoff: new Date(today).setDate(now.getDate() - 1),
    },
    {
      key: "last-7-days",
      label: "Last 7 days",
      cutoff: new Date(today).setDate(now.getDate() - 6),
    },
    {
      key: "last-14-days",
      label: "Last 14 days",
      cutoff: new Date(today).setDate(now.getDate() - 13),
    },
    { key: "older", label: "Older", cutoff: -Infinity },
  ]
  const groups: Array<SidebarThreadGroup> = []
  let remaining = sortSidebarThreads(
    threads.filter(
      (item) => !item.waitingForDelegatedWork && item.status !== "running"
    )
  )
  for (const range of ranges) {
    const cutoff = range.cutoff
    const items = remaining.filter((thread) => thread.updatedAt >= cutoff)
    if (!items.length) continue

    // Yesterday cannot include a sparse Today that has not been emitted yet.
    if (
      range.key === "yesterday" &&
      items.some((thread) => thread.updatedAt >= today)
    ) {
      continue
    }

    // Small groups are merged with the previous one if possible.
    if (items.length < 3 && items.length < remaining.length) continue

    // Reserve the fourth section for all remaining older activity.
    if (groups.length === 3 && items.length < remaining.length) continue

    if (items.length < 3 && groups.length > 0) {
      const previous = groups.pop()
      if (previous) items.unshift(...previous.threads)
      if (range.key === "yesterday") {
        groups.push({
          key: "last-7-days",
          label: "Last 7 days",
          threads: items,
        })
      } else {
        groups.push({ key: range.key, label: range.label, threads: items })
      }
    } else {
      groups.push({ key: range.key, label: range.label, threads: items })
    }
    remaining = remaining.filter((thread) => thread.updatedAt < cutoff)
    if (!remaining.length) break
  }
  const onlyDateGroup = groups.length === 1 ? groups[0] : undefined
  if (onlyDateGroup?.key === "older") onlyDateGroup.label = "Recents"

  return [
    ...groups,
    {
      key: "status:running",
      label: "Running",
      threads: threads.filter(
        (item) => item.status === "running" || item.waitingForDelegatedWork
      ),
    },
  ].filter((group) => group.threads.length > 0)
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
