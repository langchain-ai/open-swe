import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agentsRequest } from "@/features/agents/lib/api"
import { invalidateAgentThreadLists } from "@/features/agents/lib/queries"

export type ProjectRole = "owner" | "member"

export interface ProjectMember {
  user_id: string
  name: string
  login: string
  avatar_url: string
  role: ProjectRole
}

/** A named folder of threads, as one of its members sees it. */
export interface Project {
  id: string
  name: string
  workspace: string
  repo_full_name: string | null
  /** The viewer's own role. */
  role: ProjectRole
  members: Array<ProjectMember>
  created_at: string
  updated_at: string
}

export interface ProjectChanges {
  name?: string
  workspace?: string
  /** `""` unbinds the repository. */
  repo_full_name?: string
}

const path = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`

export const projectsApi = {
  list: () => agentsRequest<{ projects: Array<Project> }>("/projects"),
  create: (body: {
    name: string
    workspace?: string | null
    repo_full_name?: string | null
  }) =>
    agentsRequest<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  update: (projectId: string, changes: ProjectChanges) =>
    agentsRequest<Project>(path(projectId), {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
  remove: (projectId: string) =>
    agentsRequest<void>(path(projectId), { method: "DELETE" }),
  setMember: (projectId: string, person: string, role: ProjectRole) =>
    agentsRequest<Project>(`${path(projectId)}/members`, {
      method: "PUT",
      body: JSON.stringify({ person, role }),
    }),
  removeMember: (projectId: string, userId: string) =>
    agentsRequest<void>(
      `${path(projectId)}/members/${encodeURIComponent(userId)}`,
      { method: "DELETE" }
    ),
  fileThread: (threadId: string, projectId: string | null) =>
    agentsRequest<void>(
      projectId
        ? `${path(projectId)}/threads/${encodeURIComponent(threadId)}`
        : `/projects/threads/${encodeURIComponent(threadId)}`,
      { method: projectId ? "PUT" : "DELETE" }
    ),
}

export const projectKeys = { all: ["projects"] as const }

const NO_PROJECTS: Array<Project> = []

export function useProjects({ enabled = true } = {}) {
  const query = useQuery({
    queryKey: projectKeys.all,
    queryFn: async () => (await projectsApi.list()).projects,
    enabled,
  })
  return { projects: query.data ?? NO_PROJECTS, isPending: query.isPending }
}

/**
 * Applies `change` to the cached list at once and rolls it back if the request
 * fails; the sidebar's folders are refetched either way.
 */
function useProjectMutation<Vars, Result>(
  mutationFn: (vars: Vars) => Promise<Result>,
  change: (projects: Array<Project>, vars: Vars) => Array<Project>,
  errorTitle: string
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    meta: { errorTitle },
    onMutate: async (vars: Vars) => {
      await queryClient.cancelQueries({ queryKey: projectKeys.all })
      const previous = queryClient.getQueryData<Array<Project>>(projectKeys.all)
      if (previous)
        queryClient.setQueryData(projectKeys.all, change(previous, vars))
      return { previous }
    },
    onError: (_error, _vars, context) => {
      if (context?.previous)
        queryClient.setQueryData(projectKeys.all, context.previous)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: projectKeys.all })
      invalidateAgentThreadLists(queryClient)
    },
  })
}

const replace = (projects: Array<Project>, next: Project) =>
  projects.map((project) => (project.id === next.id ? next : project))

/** Creating waits for the server: the new project's id is what callers select. */
export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: projectsApi.create,
    meta: { errorTitle: "Couldn't create project" },
    onSuccess: (project) => {
      queryClient.setQueryData<Array<Project>>(projectKeys.all, (current) => [
        project,
        ...(current ?? []).filter((item) => item.id !== project.id),
      ])
      invalidateAgentThreadLists(queryClient)
    },
  })
}

export function useUpdateProject() {
  return useProjectMutation(
    (vars: { projectId: string; changes: ProjectChanges }) =>
      projectsApi.update(vars.projectId, vars.changes),
    (projects, { projectId, changes }) =>
      projects.map((project) =>
        project.id === projectId
          ? {
              ...project,
              ...(changes.name ? { name: changes.name } : {}),
              ...(changes.workspace ? { workspace: changes.workspace } : {}),
              ...(changes.repo_full_name !== undefined
                ? { repo_full_name: changes.repo_full_name || null }
                : {}),
            }
          : project
      ),
    "Couldn't update project"
  )
}

export function useDeleteProject() {
  return useProjectMutation(
    (projectId: string) => projectsApi.remove(projectId),
    (projects, projectId) =>
      projects.filter((project) => project.id !== projectId),
    "Couldn't delete project"
  )
}

export function useSetProjectMember() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: {
      projectId: string
      person: string
      role: ProjectRole
    }) => projectsApi.setMember(vars.projectId, vars.person, vars.role),
    meta: { errorTitle: "Couldn't share project" },
    onSuccess: (project) =>
      queryClient.setQueryData<Array<Project>>(projectKeys.all, (current) =>
        replace(current ?? [], project)
      ),
  })
}

export function useRemoveProjectMember() {
  return useProjectMutation(
    (vars: { projectId: string; userId: string; leaving: boolean }) =>
      projectsApi.removeMember(vars.projectId, vars.userId),
    (projects, { projectId, userId, leaving }) =>
      leaving
        ? projects.filter((project) => project.id !== projectId)
        : projects.map((project) =>
            project.id === projectId
              ? {
                  ...project,
                  members: project.members.filter(
                    (member) => member.user_id !== userId
                  ),
                }
              : project
          ),
    "Couldn't remove from project"
  )
}

export function useFileThreadInProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: { threadId: string; projectId: string | null }) =>
      projectsApi.fileThread(vars.threadId, vars.projectId),
    meta: { errorTitle: "Couldn't move thread" },
    onSettled: () => invalidateAgentThreadLists(queryClient),
  })
}
