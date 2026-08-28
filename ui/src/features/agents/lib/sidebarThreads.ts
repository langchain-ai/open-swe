import type {
  DesktopLocalActivity,
  DesktopLocalThreadSummary,
  DesktopProject,
} from "@/desktop"
import type { AgentSource, AgentStatus, AgentThread } from "./types"

export type SidebarThreadLocation = "cloud" | "local"

interface SidebarThreadItemBase {
  key: string
  id: string
  location: SidebarThreadLocation
  title: string
  projectKey: string | null
  projectLabel: string | null
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
}

export interface CloudSidebarThreadItem extends SidebarThreadItemBase {
  location: "cloud"
  thread: AgentThread
}

export interface LocalSidebarThreadItem extends SidebarThreadItemBase {
  location: "local"
  thread: DesktopLocalThreadSummary
}

export type SidebarThreadItem = CloudSidebarThreadItem | LocalSidebarThreadItem

export interface SidebarProjectOption {
  key: string
  label: string
}

export interface SidebarProjectGroup extends SidebarProjectOption {
  threads: Array<SidebarThreadItem>
}

/**
 * Identity, not display name: `owner/repo` for cloud and the checkout path for
 * local. Keying on the short label instead would merge `acme/api` with
 * `other/api`, and two local projects both called `api`, into one folder that
 * cannot be told apart.
 */
export function projectKey(identity?: string | null): string | null {
  const normalized = identity?.trim().toLowerCase()
  return normalized ? `project:${normalized}` : null
}

function pullRequestRef(
  thread: AgentThread
): { repoFullName: string; number: number } | undefined {
  const latest = thread.pullRequests?.at(-1)
  if (latest) {
    return { repoFullName: latest.repoFullName, number: latest.number }
  }
  if (!thread.pr || thread.repoFullName.split("/").length !== 2) return undefined
  return { repoFullName: thread.repoFullName, number: thread.pr.number }
}

export function cloudSidebarThread(
  thread: AgentThread
): CloudSidebarThreadItem {
  const projectLabel = thread.repo.trim() || null
  return {
    key: `cloud:${thread.id}`,
    id: thread.id,
    location: "cloud",
    title: thread.title,
    projectKey: projectKey(thread.repoFullName.trim() || projectLabel),
    projectLabel,
    model: thread.model,
    source: thread.source,
    threadCategory: thread.threadCategory,
    status: thread.status,
    viewed: thread.viewed,
    resolved: thread.resolved,
    createdAt: thread.createdAt,
    updatedAt: thread.updatedAt,
    planStatus: thread.planStatus,
    pr: thread.pr,
    prRef: pullRequestRef(thread),
    thread,
  }
}

export function localSidebarThread(
  thread: DesktopLocalThreadSummary,
  project: DesktopProject | undefined,
  activity: DesktopLocalActivity[string] | undefined
): LocalSidebarThreadItem {
  const projectLabel = project?.name.trim() || localProjectName(thread.cwd)
  return {
    key: `local:${thread.id}`,
    id: thread.id,
    location: "local",
    title: thread.title,
    projectKey: projectKey(project?.cwd ?? thread.cwd),
    projectLabel,
    model: thread.modelId ?? "Default",
    source: "dashboard",
    threadCategory: "interactive",
    status:
      activity === "running"
        ? "running"
        : activity === "error"
          ? "error"
          : thread.viewed
            ? "idle"
            : "finished",
    viewed: thread.viewed,
    resolved: thread.archived === true,
    createdAt: thread.createdAt,
    updatedAt: thread.updatedAt,
    thread,
  }
}

export function sidebarProjectOptions(
  threads: ReadonlyArray<SidebarThreadItem>,
  localProjects: ReadonlyArray<DesktopProject>
): Array<SidebarProjectOption> {
  const projects = new Map<string, string>()
  for (const thread of threads) {
    if (thread.projectKey && thread.projectLabel) {
      projects.set(thread.projectKey, thread.projectLabel)
    }
  }
  for (const project of localProjects) {
    const key = projectKey(project.cwd)
    if (key) projects.set(key, project.name)
  }
  return [...projects]
    .map(([key, label]) => ({ key, label }))
    .sort((left, right) => left.label.localeCompare(right.label))
}

/**
 * Label -> cloud project key, only where a label maps to exactly one cloud
 * project. Ambiguous labels are omitted rather than guessed at.
 */
export function cloudProjectKeysByLabel(
  items: ReadonlyArray<SidebarThreadItem>
): Map<string, string> {
  const keysByLabel = new Map<string, Set<string>>()
  for (const item of items) {
    if (item.location !== "cloud" || !item.projectKey || !item.projectLabel) {
      continue
    }
    const label = item.projectLabel.trim().toLowerCase()
    const keys = keysByLabel.get(label) ?? new Set<string>()
    keys.add(item.projectKey)
    keysByLabel.set(label, keys)
  }
  return new Map(
    [...keysByLabel]
      .filter(([, keys]) => keys.size === 1)
      .map(([label, keys]) => [label, [...keys][0] as string])
  )
}

/**
 * Fold a local checkout into the cloud project of the same name, so a repo you
 * have both in the cloud and on disk renders as one folder. Only applied when
 * the name identifies exactly one cloud project.
 */
export function applyProjectKeyAliases(
  items: ReadonlyArray<SidebarThreadItem>,
  aliases: ReadonlyMap<string, string>
): Array<SidebarThreadItem> {
  return items.map((item) => {
    if (item.location !== "local" || !item.projectLabel) return item
    const alias = aliases.get(item.projectLabel.trim().toLowerCase())
    return alias ? { ...item, projectKey: alias } : item
  })
}

/**
 * Split the sidebar into one bucket per project plus the leftovers shown under
 * "Recents". Projects keep their own most-recent-first order and are ranked by
 * their freshest thread, so the project you just worked in stays on top.
 */
export function groupSidebarThreadsByProject(
  threads: ReadonlyArray<SidebarThreadItem>,
  projects: ReadonlyArray<SidebarProjectOption>,
  mode: SidebarSort = "updated"
): { projects: Array<SidebarProjectGroup>; recents: Array<SidebarThreadItem> } {
  const buckets = new Map<string, SidebarProjectGroup>(
    projects.map((project) => [project.key, { ...project, threads: [] }])
  )
  const recents: Array<SidebarThreadItem> = []
  for (const thread of sortSidebarThreads(threads, mode)) {
    const bucket = thread.projectKey ? buckets.get(thread.projectKey) : undefined
    if (bucket) bucket.threads.push(thread)
    else recents.push(thread)
  }
  return {
    projects: [...buckets.values()]
      .filter((group) => group.threads.length > 0)
      .sort(
        (left, right) =>
          (right.threads[0]?.updatedAt ?? 0) - (left.threads[0]?.updatedAt ?? 0)
      ),
    recents,
  }
}

export function filterSidebarProject(
  threads: ReadonlyArray<SidebarThreadItem>,
  selectedProjectKey: string | null
): Array<SidebarThreadItem> {
  return selectedProjectKey
    ? threads.filter((thread) => thread.projectKey === selectedProjectKey)
    : [...threads]
}

export type SidebarSort = "priority" | "updated" | "manual"

/**
 * Priority ranks what still wants the user's attention: live runs first, then
 * anything unread, then everything else. Within a rank it falls back to
 * recency, so the ordering only ever reorders across those three bands.
 */
function priorityRank(thread: SidebarThreadItem): number {
  if (thread.status === "running") return 0
  return thread.viewed ? 2 : 1
}

function byRecency(
  left: SidebarThreadItem,
  right: SidebarThreadItem
): number {
  return (
    right.updatedAt - left.updatedAt ||
    right.createdAt - left.createdAt ||
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
  if (mode === "updated") return [...threads].sort(byRecency)
  return [...threads].sort(
    (left, right) =>
      priorityRank(left) - priorityRank(right) || byRecency(left, right)
  )
}

function localProjectName(cwd: string): string | null {
  const segments = cwd.split(/[\\/]/).filter(Boolean)
  return segments.at(-1) ?? null
}
