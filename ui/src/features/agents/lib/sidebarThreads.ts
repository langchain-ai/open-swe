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

export function projectKey(name?: string | null): string | null {
  const normalized = name?.trim().toLowerCase()
  return normalized ? `project:${normalized}` : null
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
    projectKey: projectKey(projectLabel),
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
    projectKey: projectKey(projectLabel),
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
    resolved: false,
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
    const key = projectKey(project.name)
    if (key) projects.set(key, project.name)
  }
  return [...projects]
    .map(([key, label]) => ({ key, label }))
    .sort((left, right) => left.label.localeCompare(right.label))
}

export function filterSidebarProject(
  threads: ReadonlyArray<SidebarThreadItem>,
  selectedProjectKey: string | null
): Array<SidebarThreadItem> {
  return selectedProjectKey
    ? threads.filter((thread) => thread.projectKey === selectedProjectKey)
    : [...threads]
}

export function sortSidebarThreads(
  threads: ReadonlyArray<SidebarThreadItem>
): Array<SidebarThreadItem> {
  return [...threads].sort(
    (left, right) =>
      right.updatedAt - left.updatedAt ||
      right.createdAt - left.createdAt ||
      left.key.localeCompare(right.key)
  )
}

function localProjectName(cwd: string): string | null {
  const segments = cwd.split(/[\\/]/).filter(Boolean)
  return segments.at(-1) ?? null
}
