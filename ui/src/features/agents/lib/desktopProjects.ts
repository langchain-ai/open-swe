import { useCallback, useEffect, useState } from "react"

import type { DesktopProject } from "@/desktop"
import { localProjectsApi } from "@/features/agents/lib/localProjectsApi"

/**
 * Projects come from the local server rather than Electron IPC, so the desktop
 * app and a plain `open-swe` server behave the same.
 */
export function useDesktopProjects() {
  const [projects, setProjects] = useState<Array<DesktopProject>>([])

  useEffect(() => {
    let cancelled = false
    void localProjectsApi
      .list()
      .then((value) => !cancelled && setProjects(value))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const addProject = useCallback(async (cwd: string) => {
    const project = await localProjectsApi.add(cwd)
    setProjects((current) => [
      project,
      ...current.filter((item) => item.cwd !== project.cwd),
    ])
    return project
  }, [])

  const removeProject = useCallback(async (cwd: string) => {
    const { removed } = await localProjectsApi.remove(cwd)
    if (removed) {
      setProjects((current) => current.filter((project) => project.cwd !== cwd))
    }
    return removed
  }, [])

  return { projects, addProject, removeProject }
}
