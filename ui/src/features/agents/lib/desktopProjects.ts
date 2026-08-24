import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type { LocalProject } from "@/features/agents/lib/localQueries"
import {
  addLocalProject,
  removeLocalProject,
} from "@/features/agents/lib/localFunctions"
import {
  localKeys,
  localProjectsQuery,
} from "@/features/agents/lib/localQueries"

const NO_PROJECTS: Array<LocalProject> = []

/**
 * Projects come from the local server rather than Electron IPC, so the desktop
 * app and a plain `open-swe` server behave the same.
 */
export function useDesktopProjects() {
  const queryClient = useQueryClient()
  const projects = useQuery(localProjectsQuery())

  const add = useMutation({
    mutationFn: (cwd: string) => addLocalProject({ data: cwd }),
    onSuccess: (project) =>
      queryClient.setQueryData<Array<LocalProject>>(
        localKeys.projects,
        (current = NO_PROJECTS) => [
          project,
          ...current.filter((item) => item.cwd !== project.cwd),
        ]
      ),
  })

  const remove = useMutation({
    mutationFn: (cwd: string) => removeLocalProject({ data: cwd }),
    onSuccess: ({ removed }, cwd) => {
      if (!removed) return
      queryClient.setQueryData<Array<LocalProject>>(
        localKeys.projects,
        (current = NO_PROJECTS) =>
          current.filter((project) => project.cwd !== cwd)
      )
    },
  })

  return {
    projects: projects.data ?? NO_PROJECTS,
    addProject: add.mutateAsync,
    removeProject: async (cwd: string) =>
      (await remove.mutateAsync(cwd)).removed,
  }
}
