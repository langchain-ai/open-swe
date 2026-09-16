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
  /** Every project the thread belongs to; a multi-repo thread lists under each. */
  projectKeys: Array<string>
  projectLabels: Array<string>
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
export function sidebarProjectKey(identity?: string | null): string | null {
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
  // Without a PR record the repo is only unambiguous when the thread has one.
  const [only] = thread.repos
  if (!thread.pr || thread.repos.length !== 1 || !only) return undefined
  if (only.split("/").length !== 2) return undefined
  return { repoFullName: only, number: thread.pr.number }
}

/** Short repo name, so a cloud project folds together with a local checkout. */
function repoShortName(fullName: string): string {
  return fullName.split("/").at(-1)?.trim() ?? ""
}

export function cloudSidebarThread(
  thread: AgentThread
): CloudSidebarThreadItem {
  const repos = thread.repos
    .map((repo) => repo.trim())
    .filter((repo) => repo.length > 0)
  const projectKeys: Array<string> = []
  const projectLabels: Array<string> = []
  for (const repo of repos) {
    const key = sidebarProjectKey(repo)
    const label = repoShortName(repo) || repo
    if (!key || projectKeys.includes(key)) continue
    projectKeys.push(key)
    projectLabels.push(label)
  }
  return {
    key: `cloud:${thread.id}`,
    id: thread.id,
    location: "cloud",
    title: thread.title,
    projectKeys,
    projectLabels,
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
  const projectKey = sidebarProjectKey(project?.cwd ?? thread.cwd)
  return {
    key: `local:${thread.id}`,
    id: thread.id,
    location: "local",
    title: thread.title,
    projectKeys: projectKey ? [projectKey] : [],
    projectLabels: projectLabel ? [projectLabel] : [],
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
    thread.projectKeys.forEach((key, index) => {
      const label = thread.projectLabels[index]
      if (key && label) projects.set(key, label)
    })
  }
  for (const project of localProjects) {
    const key = sidebarProjectKey(project.cwd)
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
    if (item.location !== "cloud") continue
    item.projectKeys.forEach((key, index) => {
      const rawLabel = item.projectLabels[index]
      if (!key || !rawLabel) return
      const label = rawLabel.trim().toLowerCase()
      const keys = keysByLabel.get(label) ?? new Set<string>()
      keys.add(key)
      keysByLabel.set(label, keys)
    })
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
    if (item.location !== "local") return item
    const projectKeys = item.projectKeys.map((key, index) => {
      const label = item.projectLabels[index]
      return (label && aliases.get(label.trim().toLowerCase())) || key
    })
    return { ...item, projectKeys }
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
    let bucketed = false
    for (const key of thread.projectKeys) {
      const bucket = buckets.get(key)
      if (!bucket) continue
      bucket.threads.push(thread)
      bucketed = true
    }
    if (!bucketed) recents.push(thread)
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

function localProjectName(cwd: string): string | null {
  const segments = cwd.split(/[\\/]/).filter(Boolean)
  return segments.at(-1) ?? null
}
