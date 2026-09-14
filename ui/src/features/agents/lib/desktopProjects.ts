import { useCallback, useEffect, useState } from "react"

import type { DesktopProject } from "@/desktop"

export function useDesktopProjects() {
  const [projects, setProjects] = useState<Array<DesktopProject>>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    const unsubscribe = desktop.onProjectsChanged(setProjects)
    void desktop.listProjects().then((nextProjects) => {
      setProjects(nextProjects)
      setLoaded(true)
    })
    return unsubscribe
  }, [])

  const addProject = useCallback(async () => {
    const project = await window.openSweDesktop?.addProject()
    if (project) {
      setProjects((current) => [
        project,
        ...current.filter((item) => item.cwd !== project.cwd),
      ])
    }
    return project ?? null
  }, [])

  const removeProject = useCallback(async (cwd: string) => {
    const removed = (await window.openSweDesktop?.removeProject(cwd)) ?? false
    if (removed) {
      setProjects((current) => current.filter((project) => project.cwd !== cwd))
    }
    return removed
  }, [])

  return { projects, loaded, addProject, removeProject }
}
