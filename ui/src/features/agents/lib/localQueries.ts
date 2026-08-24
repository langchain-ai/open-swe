import { queryOptions } from "@tanstack/react-query"

import type { DirectoryListing } from "../../../../server/local/browse"
import type { LocalProject } from "../../../../server/local/project-store"
import type { LocalThread } from "../../../../server/local/thread-store"
import {
  browseLocalDirectories,
  getLocalThread,
  listLocalBranches,
  listLocalProjects,
  listLocalThreads,
  localThreadActivity,
  localThreadBranchDiff,
  localThreadDiff,
} from "@/features/agents/lib/localFunctions"
import { isLocalRuntime } from "@/lib/desktop-local-mode"

export type { DirectoryListing, LocalProject, LocalThread }

export const localKeys = {
  projects: ["local-projects"] as const,
  browse: (path: string | null) => ["local-browse", path] as const,
  branches: (cwd: string | null) => ["local-branches", cwd] as const,
  threads: ["local-threads"] as const,
  thread: (id: string) => ["local-threads", id] as const,
  threadReady: (id: string) => ["local-thread-ready", id] as const,
  activity: ["local-thread-activity"] as const,
  diff: (id: string) => ["local-thread-diff", id] as const,
  branchDiff: (id: string) => ["local-thread-pr-diff", id] as const,
}

export const localProjectsQuery = () =>
  queryOptions({
    queryKey: localKeys.projects,
    queryFn: () => listLocalProjects(),
    enabled: isLocalRuntime(),
  })

export const localBrowseQuery = (path: string | null, enabled: boolean) =>
  queryOptions({
    queryKey: localKeys.browse(path),
    queryFn: () => browseLocalDirectories({ data: path }),
    enabled: enabled && isLocalRuntime(),
    staleTime: 0,
  })

export const localBranchesQuery = (cwd: string | null) =>
  queryOptions({
    queryKey: localKeys.branches(cwd),
    queryFn: () => listLocalBranches({ data: cwd as string }),
    enabled: Boolean(cwd) && isLocalRuntime(),
    refetchOnWindowFocus: true,
  })

export const localThreadsQuery = (enabled?: boolean) =>
  queryOptions({
    queryKey: localKeys.threads,
    queryFn: () => listLocalThreads(),
    enabled: enabled !== false && isLocalRuntime(),
    refetchInterval: enabled === false ? false : 1000,
  })

export const localThreadQuery = (id: string) =>
  queryOptions({
    queryKey: localKeys.thread(id),
    queryFn: () => getLocalThread({ data: id }),
    enabled: isLocalRuntime(),
  })

export const localActivityQuery = () =>
  queryOptions({
    queryKey: localKeys.activity,
    queryFn: () => localThreadActivity(),
    enabled: isLocalRuntime(),
    refetchInterval: 1000,
  })

export const localDiffQuery = (
  id: string,
  enabled: boolean,
  isRunning: boolean
) =>
  queryOptions({
    queryKey: localKeys.diff(id),
    queryFn: () => localThreadDiff({ data: id }),
    enabled: enabled && isLocalRuntime(),
    refetchInterval: isRunning ? 5000 : false,
  })

/**
 * What the thread's branch has committed on top of its pull request's base.
 * Unlike the checkpoint diff this ignores the worktree, which every session in
 * the project shares.
 */
export const localBranchDiffQuery = (
  id: string,
  enabled: boolean,
  isRunning: boolean
) =>
  queryOptions({
    queryKey: localKeys.branchDiff(id),
    queryFn: () => localThreadBranchDiff({ data: id }),
    enabled: enabled && isLocalRuntime(),
    refetchInterval: isRunning ? 5000 : false,
  })
