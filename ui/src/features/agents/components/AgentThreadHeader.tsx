import { FolderOpen } from "lucide-react"

import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { cn } from "@/lib/utils"

export function AgentThreadHeader({
  project,
  target,
  panelCollapsed,
}: {
  project?: string | null
  target: "Cloud" | "This Mac"
  panelCollapsed: boolean
}) {
  const sidebarCollapsed = useSidebarCollapsed()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  return (
    <header
      data-desktop-drag-region=""
      className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
    >
      <div
        className={cn(
          "flex h-full w-full items-center gap-3 px-4",
          sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14"),
          panelCollapsed && "pr-14"
        )}
      >
        {project && (
          <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
            <FolderOpen className="size-3.5 shrink-0" />
            <span className="truncate" title={project}>
              {project}
            </span>
          </span>
        )}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {target}
        </span>
      </div>
    </header>
  )
}
