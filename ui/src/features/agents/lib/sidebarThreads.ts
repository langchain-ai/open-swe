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
  hasEdits?: AgentThread["hasEdits"]
  pullRequests?: AgentThread["pullRequests"]
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
 * Identity, not display name: `owner/repo`. Keying on the short label instead
 * would merge `acme/api` with `other/api` into one folder that cannot be told
 * apart.
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
    hasEdits: thread.hasEdits,
    pullRequests: thread.pullRequests,
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
    subagents: thread.subagents,
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
