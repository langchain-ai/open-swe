import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"

import type { DesktopLocalThread, DesktopProject } from "@/desktop"
import { invalidateAgentThreadLists } from "@/features/agents/lib/queries"
import { localThreadKeys } from "@/features/agents/lib/desktopLocal"
import { projectKeys, projectsApi } from "@/features/projects/lib/projects"

/**
 * Files this Mac's folders under Open SWE projects. A folder that is not a
 * clone of a GitHub repository gets a project of its own the first time it is
 * seen, and the This Mac threads already working in it move there, so every
 * device files them the same way. The folder's path stays on this Mac.
 */
export function useLinkDesktopFolders(
  folders: Array<DesktopProject>,
  localThreads: Array<DesktopLocalThread> | undefined,
  enabled: boolean
) {
  const queryClient = useQueryClient()
  const attempted = useRef(new Set<string>())
  useEffect(() => {
    const desktop = window.openSweDesktop
    const linkProject = desktop?.linkProject
    if (!enabled || !linkProject || !localThreads) return
    for (const folder of folders) {
      // `undefined` is a desktop build that doesn't know; only `null` is "none".
      if (folder.repo !== null || folder.projectId !== null) continue
      if (attempted.current.has(folder.cwd)) continue
      attempted.current.add(folder.cwd)
      void (async () => {
        const project = await projectsApi.create({ name: folder.name })
        await linkProject({ cwd: folder.cwd, projectId: project.id })
        await Promise.all(
          localThreads
            .filter((thread) => thread.cwd === folder.cwd)
            .map((thread) => projectsApi.fileThread(thread.id, project.id))
        )
      })()
        .catch((cause: unknown) =>
          console.warn("Could not give a This Mac folder a project", cause)
        )
        .finally(() => {
          void queryClient.invalidateQueries({ queryKey: projectKeys.all })
          void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
          invalidateAgentThreadLists(queryClient)
        })
    }
  }, [enabled, folders, localThreads, queryClient])
}
