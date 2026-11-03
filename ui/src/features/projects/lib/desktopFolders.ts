import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"

import type { DesktopLocalThread, DesktopProject } from "@/desktop"
import { invalidateAgentThreadLists } from "@/features/agents/lib/queries"
import { localThreadKeys } from "@/features/agents/lib/desktopLocal"
import { projectKeys, projectsApi } from "@/features/projects/lib/projects"

/**
 * One link per folder, shared by the sidebar and the composer, so a thread
 * started while its folder is being linked still lands in the project.
 */
const links = new Map<string, Promise<string | null>>()

/** Whether the folder should have a project of its own and has none yet. */
function needsProject(folder: DesktopProject) {
  // `undefined` is a desktop build that doesn't know; only `null` is "none".
  return (
    folder.repo === null &&
    folder.projectId === null &&
    Boolean(window.openSweDesktop?.linkProject)
  )
}

/**
 * The Open SWE project a This Mac folder belongs to. A folder that is not a
 * clone of a GitHub repository gets one of its own the first time, and the
 * folder's This Mac threads move into it, so every device files them the
 * same way. The folder's path stays on this Mac.
 */
export function useFolderProject() {
  const queryClient = useQueryClient()
  return (
    folder: DesktopProject,
    localThreads: Array<DesktopLocalThread> = []
  ): Promise<string | null> => {
    if (!needsProject(folder)) return Promise.resolve(folder.projectId ?? null)
    const pending = links.get(folder.cwd)
    if (pending) return pending
    const link = (async () => {
      const project = await projectsApi.create({ name: folder.name })
      await window.openSweDesktop?.linkProject?.({
        cwd: folder.cwd,
        projectId: project.id,
      })
      await Promise.all(
        localThreads
          .filter((thread) => thread.cwd === folder.cwd)
          .map((thread) => projectsApi.fileThread(thread.id, project.id))
      )
      return project.id
    })()
      .catch((cause: unknown) => {
        // Not retried this session: each attempt would refetch and try again.
        console.warn("Could not give a This Mac folder a project", cause)
        return null
      })
      .finally(() => {
        void queryClient.invalidateQueries({ queryKey: projectKeys.all })
        void queryClient.invalidateQueries({ queryKey: localThreadKeys.all })
        invalidateAgentThreadLists(queryClient)
      })
    links.set(folder.cwd, link)
    return link
  }
}

/** Links each of this Mac's folders that needs a project, once its threads are known. */
export function useLinkDesktopFolders(
  folders: Array<DesktopProject>,
  localThreads: Array<DesktopLocalThread> | undefined,
  enabled: boolean
) {
  const folderProject = useFolderProject()
  useEffect(() => {
    if (!enabled || !localThreads) return
    for (const folder of folders)
      if (needsProject(folder)) void folderProject(folder, localThreads)
  }, [enabled, folders, localThreads, folderProject])
}
