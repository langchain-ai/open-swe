import { Folder } from "lucide-react"
import { useState } from "react"

import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import type { DesktopLocalThreadSummary } from "@/desktop"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { useSidebarProjects } from "@/features/agents/lib/queries"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import type { AgentThread } from "@/features/agents/lib/types"
import { useSession } from "@/lib/session"

export function ThreadProjectIndicator({
  thread,
  localThread,
}: {
  thread?: AgentThread
  localThread?: DesktopLocalThreadSummary
}) {
  const [open, setOpen] = useState(false)
  const { projects } = useDesktopProjects()
  const { prefs } = useSidebarPrefs()
  const session = useSession()
  const cloudProjects = useSidebarProjects({
    ...prefs.filters,
    enabled: !localThread && Boolean(session.data),
  })
  const repo = !localThread ? thread?.repoFullName.trim() : undefined
  const projectName = localThread
    ? projects.find((project) => project.cwd === localThread.cwd)?.name
    : (cloudProjects.data?.find(
        (project) => project.repoFullName.toLowerCase() === repo?.toLowerCase()
      )?.name ?? (repo ? thread?.repo || repo : undefined))
  if (!projectName) return null

  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        render={<button type="button" />}
        closeOnClick={false}
        onClick={() => setOpen(true)}
        onPointerLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        aria-label={`Project: ${projectName}`}
        data-no-drag=""
        className="flex size-7 shrink-0 items-center justify-center text-muted-foreground"
      >
        <Folder className="size-4" />
      </TooltipTrigger>
      <TooltipPopup>{projectName}</TooltipPopup>
    </Tooltip>
  )
}
