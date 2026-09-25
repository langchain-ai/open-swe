import type {
  DesktopLocalActivity,
  DesktopLocalThreadSummary,
  DesktopProject,
} from "@/desktop"
import type {
  AgentSource,
  AgentStatus,
  AgentThread,
  ReviewPageRef,
} from "./types"

export type SidebarThreadLocation = "cloud" | "local"

interface SidebarThreadItemBase {
  key: string
  id: string
  location: SidebarThreadLocation
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

export function cloudSidebarThread(
  thread: AgentThread
): CloudSidebarThreadItem {
  const repoLabel = thread.repo.trim() || null
  return {
    key: `cloud:${thread.id}`,
    id: thread.id,
    location: "cloud",
    title: thread.title,
    repoKey: sidebarRepoKey(thread.repoFullName.trim() || repoLabel),
    repoLabel,
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
    reviewPage: thread.reviewPage,
    thread,
  }
}

export function localSidebarThread(
  thread: DesktopLocalThreadSummary,
  repo: DesktopProject | undefined,
  activity: DesktopLocalActivity[string] | undefined
): LocalSidebarThreadItem {
  const repoLabel = repo?.name.trim() || localRepoName(thread.cwd)
  return {
    key: `local:${thread.id}`,
    id: thread.id,
    location: "local",
    title: thread.title,
    repoKey: sidebarRepoKey(repo?.cwd ?? thread.cwd),
    repoLabel,
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

export function sidebarRepoOptions(
  threads: ReadonlyArray<SidebarThreadItem>,
  localRepos: ReadonlyArray<DesktopProject>
): Array<SidebarRepoOption> {
  const repos = new Map<string, string>()
  for (const thread of threads) {
    if (thread.repoKey && thread.repoLabel) {
      repos.set(thread.repoKey, thread.repoLabel)
    }
  }
  for (const repo of localRepos) {
    const key = sidebarRepoKey(repo.cwd)
    if (key) repos.set(key, repo.name)
  }
  return [...repos]
    .map(([key, label]) => ({ key, label }))
    .sort((left, right) => left.label.localeCompare(right.label))
}

/**
 * Label -> cloud repo key, only where a label maps to exactly one cloud
 * repo. Ambiguous labels are omitted rather than guessed at.
 */
export function cloudRepoKeysByLabel(
  items: ReadonlyArray<SidebarThreadItem>
): Map<string, string> {
  const keysByLabel = new Map<string, Set<string>>()
  for (const item of items) {
    if (item.location !== "cloud" || !item.repoKey || !item.repoLabel) {
      continue
    }
    const label = item.repoLabel.trim().toLowerCase()
    const keys = keysByLabel.get(label) ?? new Set<string>()
    keys.add(item.repoKey)
    keysByLabel.set(label, keys)
  }
  return new Map(
    [...keysByLabel]
      .filter(([, keys]) => keys.size === 1)
      .map(([label, keys]) => [label, [...keys][0] as string])
  )
}

/**
 * Fold a local checkout into the cloud repo of the same name, so a repo you
 * have both in the cloud and on disk renders as one folder. Only applied when
 * the name identifies exactly one cloud repo.
 */
export function applyRepoKeyAliases(
  items: ReadonlyArray<SidebarThreadItem>,
  aliases: ReadonlyMap<string, string>
): Array<SidebarThreadItem> {
  return items.map((item) => {
    if (item.location !== "local" || !item.repoLabel) return item
    const alias = aliases.get(item.repoLabel.trim().toLowerCase())
    return alias ? { ...item, repoKey: alias } : item
  })
}

/**
 * Split the sidebar into one bucket per repo plus the leftovers shown under
 * "Recents". Repositories keep their own most-recent-first order and are ranked by
 * their freshest thread, so the repo you just worked in stays on top.
 */
export function groupSidebarThreadsByRepo(
  threads: ReadonlyArray<SidebarThreadItem>,
  repos: ReadonlyArray<SidebarRepoOption>,
  mode: SidebarSort = "updated"
): { repos: Array<SidebarRepoGroup>; recents: Array<SidebarThreadItem> } {
  const buckets = new Map<string, SidebarRepoGroup>(
    repos.map((repo) => [repo.key, { ...repo, threads: [] }])
  )
  const recents: Array<SidebarThreadItem> = []
  for (const thread of sortSidebarThreads(threads, mode)) {
    const bucket = thread.repoKey ? buckets.get(thread.repoKey) : undefined
    if (bucket) bucket.threads.push(thread)
    else recents.push(thread)
  }
  return {
    repos: [...buckets.values()]
      .filter((group) => group.threads.length > 0)
      .sort(
        (left, right) =>
          (right.threads[0]?.updatedAt ?? 0) - (left.threads[0]?.updatedAt ?? 0)
      ),
    recents,
  }
}

/**
 * Buckets already-built repo groups by the workspace that owns them, using
 * each entry's `workspace` field from `repos` — falling back to
 * {@link DEFAULT_SIDEBAR_WORKSPACE_SLUG} for a repo with no workspace
 * (every repository belongs to exactly one workspace, so this only fires for
 * a repo the caller didn't annotate). A workspace absent from `workspaces`
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

/**
 * Groups threads by repo the way {@link groupSidebarThreadsByRepo}
 * does, then nests those repo groups under the workspace that owns each
 * one. Threads with no repo at all are unaffected by workspace grouping
 * and stay in `recents`, same as the repo-only grouping.
 */
export function groupSidebarThreadsByWorkspace(
  threads: ReadonlyArray<SidebarThreadItem>,
  repos: ReadonlyArray<SidebarWorkspacedRepoOption>,
  workspaces: ReadonlyArray<{ slug: string; name: string }>,
  mode: SidebarSort = "updated"
): {
  workspaces: Array<SidebarWorkspaceGroup>
  recents: Array<SidebarThreadItem>
} {
  const grouped = groupSidebarThreadsByRepo(threads, repos, mode)
  return {
    workspaces: groupRepoGroupsByWorkspace(grouped.repos, repos, workspaces),
    recents: grouped.recents,
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

function localRepoName(cwd: string): string | null {
  const segments = cwd.split(/[\\/]/).filter(Boolean)
  return segments.at(-1) ?? null
}
