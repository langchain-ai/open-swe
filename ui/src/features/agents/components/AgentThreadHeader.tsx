import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { cn } from "@/lib/utils"

export function AgentThreadHeader({
  target,
  panelCollapsed,
}: {
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
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {target}
        </span>
      </div>
    </header>
  )
}
