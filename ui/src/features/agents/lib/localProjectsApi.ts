import type {
  DesktopLocalDiff,
  DesktopLocalPromptInput,
  DesktopLocalThreadSummary,
  DesktopProject,
} from "@/desktop"
import {
  addLocalProject,
  browseLocalDirectories,
  checkoutLocalBranch,
  clearLocalThreadPrompt,
  deleteLocalThread,
  getLocalThread,
  listLocalBranches,
  listLocalProjects,
  listLocalThreads,
  localThreadActivity,
  localThreadBranchDiff,
  localThreadDiff,
  localThreadPrompt,
  removeLocalProject,
  startLocalThread,
  updateLocalThread,
} from "@/features/agents/lib/localFunctions"

export interface DirectoryEntry {
  name: string
  path: string
  isRepository: boolean
}

export interface DirectoryListing {
  path: string
  parent: string | null
  entries: Array<DirectoryEntry>
}

/**
 * The local project list. Served by the same local server that serves this page,
 * so the desktop app and a plain `open-swe` server share one implementation.
 */
export const localProjectsApi = {
  list: () => listLocalProjects() as Promise<Array<DesktopProject>>,
  add: (cwd: string) =>
    addLocalProject({ data: cwd }) as Promise<DesktopProject>,
  remove: (cwd: string) => removeLocalProject({ data: cwd }),
  browse: (path?: string) =>
    browseLocalDirectories({
      data: path ?? null,
    }) as Promise<DirectoryListing>,
  branches: (cwd: string) => listLocalBranches({ data: cwd }),
  checkout: (cwd: string, branch: string, create = false) =>
    checkoutLocalBranch({ data: { cwd, branch, create } }),
}

export interface LocalThreadPrompt {
  prompt: string
  images: Array<unknown>
  skills: Array<unknown>
}

export const localThreadsApi = {
  list: () => listLocalThreads() as Promise<Array<DesktopLocalThreadSummary>>,
  get: (id: string) =>
    (
      getLocalThread({ data: id }) as Promise<DesktopLocalThreadSummary | null>
    ).catch(() => null),
  create: (input: Record<string, unknown>) =>
    startLocalThread({ data: input }) as Promise<DesktopLocalThreadSummary>,
  update: (id: string, patch: Record<string, unknown>) =>
    updateLocalThread({
      data: { id, patch },
    }) as Promise<DesktopLocalThreadSummary>,
  remove: (id: string) => deleteLocalThread({ data: id }),
  diff: (id: string) =>
    localThreadDiff({ data: id }) as Promise<DesktopLocalDiff>,
  prDiff: (id: string) =>
    localThreadBranchDiff({ data: id }) as Promise<DesktopLocalDiff>,
  activity: () => localThreadActivity(),
  prompt: (id: string) =>
    localThreadPrompt({ data: id }) as Promise<DesktopLocalPromptInput | null>,
  clearPrompt: (id: string) =>
    clearLocalThreadPrompt({
      data: id,
    }) as Promise<DesktopLocalThreadSummary | null>,
}
